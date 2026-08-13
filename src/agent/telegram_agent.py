"""Telegram 대화형 트레이딩 에이전트 (long-poll 데몬).

챗 입력 → 조회/분석(claude·pi CLI) + 매수·매도(인라인 버튼 확인 필수).

안전선:
  - KIS paper 한정 (mode="paper")
  - 인가된 chat_id(TELEGRAM_CHAT_ID)만 처리, 그 외 무시
  - 모든 주문은 인라인 버튼 확인 후에만 실행 (자연어/LLM이 자동 발주 금지)
  - 주문 notional 상한(fat-finger 방지)
  - 모든 명령/주문 감사로그(data/logs/telegram_agent_audit.jsonl)

명령:
  /help /balance(/잔고) /positions(/포지션) /status(/현황)
  /buy <6자리코드> <수량>  /sell <6자리코드> <수량>
  자연어 → LLM 분석/의도파싱 → 분석 답변 또는 주문 제안(버튼 확인)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from src.broker.kis_client import KisClient
from src.llm.cli_client import call_llm
from src.storage.repository import Repo
from src.util.keychain import load_kis_keys, load_telegram_keys

# ── 안전 상수 ────────────────────────────────────────────────
MAX_ORDER_NOTIONAL_KRW = 10_000_000   # 주문당 명목금액 상한 (fat-finger 방지)
PENDING_TTL_SEC = 300                  # 확인 대기 주문 만료
POLL_TIMEOUT_SEC = 30                  # long-poll
LLM_TIMEOUT_SEC = 90

API_BASE = "https://api.telegram.org"

Path("data/logs").mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("data/logs/telegram_agent.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("stock-trader.telegram_agent")
_AUDIT = Path("data/logs/telegram_agent_audit.jsonl")


def _audit(event: str, **kw) -> None:
    rec = {"ts": datetime.now().astimezone().isoformat(), "event": event, **kw}
    try:
        with _AUDIT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("audit write 실패: %s", e)


# ── 모드(paper/real) 상태 + 키 로딩 ─────────────────────────
_MODE_FILE = Path("data/agent_mode")


def _load_mode() -> str:
    try:
        m = _MODE_FILE.read_text(encoding="utf-8").strip().lower()
        if m in ("paper", "real"):
            return m
    except OSError:
        pass
    return "paper"


def _save_mode(mode: str) -> None:
    try:
        _MODE_FILE.write_text(mode, encoding="utf-8")
    except OSError as e:
        log.warning("mode 저장 실패: %s", e)


def _force_load_keys(mode: str) -> bool:
    """해당 mode의 KIS 키를 keychain→env로 강제 재주입(기존 env 제거 후). 성공 여부."""
    for k in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_CANO", "KIS_ACNT_PRDT_CD"):
        os.environ.pop(k, None)
    os.environ["KIS_MODE"] = mode
    load_kis_keys(mode)
    return bool(
        os.environ.get("KIS_APP_KEY")
        and os.environ.get("KIS_APP_SECRET")
        and os.environ.get("KIS_CANO")
    )


# ── Telegram API ────────────────────────────────────────────
class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = str(chat_id)

    def _url(self, method: str) -> str:
        return f"{API_BASE}/bot{self.token}/{method}"

    def get_me(self) -> dict:
        r = requests.get(self._url("getMe"), timeout=10)
        return r.json().get("result", {})

    def get_updates(self, offset: int) -> list[dict]:
        try:
            r = requests.get(
                self._url("getUpdates"),
                params={"offset": offset, "timeout": POLL_TIMEOUT_SEC},
                timeout=POLL_TIMEOUT_SEC + 10,
            )
            return r.json().get("result", []) or []
        except requests.RequestException as e:
            log.warning("getUpdates 오류: %s", e)
            time.sleep(3)
            return []

    def send(self, text: str, reply_markup: dict | None = None) -> None:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        try:
            r = requests.post(self._url("sendMessage"), json=payload, timeout=10)
            if r.status_code != 200:
                # Markdown 파싱 실패 시 plain 재시도
                payload.pop("parse_mode", None)
                requests.post(self._url("sendMessage"), json=payload, timeout=10)
        except requests.RequestException as e:
            log.warning("send 오류: %s", e)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        try:
            requests.post(
                self._url("answerCallbackQuery"),
                json={"callback_query_id": callback_id, "text": text},
                timeout=10,
            )
        except requests.RequestException:
            pass

    def set_commands(self) -> None:
        cmds = [
            {"command": "balance", "description": "잔고 조회 (국내+해외)"},
            {"command": "positions", "description": "보유 포지션 상세"},
            {"command": "status", "description": "계좌 현황 요약"},
            {"command": "buyable", "description": "해외 매수가능금액 조회"},
            {"command": "history", "description": "거래 내역 (최근 N건 + 손익 요약)"},
            {"command": "mode", "description": "paper/real 모드 전환·확인"},
            {"command": "buy", "description": "매수: /buy <코드> <수량>"},
            {"command": "sell", "description": "매도: /sell <코드> <수량>"},
            {"command": "help", "description": "명령어 도움말"},
        ]
        try:
            requests.post(self._url("setMyCommands"), json={"commands": cmds}, timeout=10)
        except requests.RequestException:
            pass


# ── 포맷 헬퍼 ────────────────────────────────────────────────
def _fmt_won(v) -> str:
    return f"{int(v):,}원"


def _pnl_totals(positions, scale: int = 1):
    """(invested, current_eval, pnl, pct). scale로 통화 단위 환산(해외=100)."""
    invested = sum(p["avg_price"] * p["qty"] for p in positions)
    cur = sum(p["eval_amt"] for p in positions)
    pnl = cur - invested
    pct = (pnl / invested * 100) if invested else 0.0
    return invested / scale, cur / scale, pnl / scale, pct


def _balance_text(dom, ov) -> str:
    lines = ["*📊 잔고*"]
    if dom:
        lines.append(f"국내: 예수금 {_fmt_won(dom.cash)} / 총평가 {_fmt_won(dom.total_eval)}")
        lines.append(f"  보유 {len(dom.positions)}종목")
    else:
        lines.append("국내: 조회 실패")
    if ov is None:
        lines.append("해외(USD): 조회 실패 (rate limit/네트워크)")
    else:
        scale = max(ov.price_scale, 1)
        lines.append(f"해외(USD): 현금 {ov.cash / scale:,.2f} / 보유 {len(ov.positions)}종목")
    return "\n".join(lines)


def _positions_text(dom, ov) -> str:
    lines = ["*📈 보유 포지션*"]
    lines.append("— 국내 —")
    rows = list(dom.positions) if dom else []
    if dom is None:
        lines.append("조회 실패")
    elif not rows:
        lines.append("보유 없음")
    for p in rows:
        pnl_amt = p["eval_amt"] - p["avg_price"] * p["qty"]
        lines.append(
            f"`{p['ticker']}` {p['name']} {p['qty']}주 "
            f"@{_fmt_won(p['avg_price'])} → {_fmt_won(p['current_price'])} "
            f"({pnl_amt:+,}원, {p['pnl_pct']:+.2f}%)"
        )
    if rows:
        _, _, pnl, pct = _pnl_totals(rows)
        lines.append(f"  └ 국내 합계: {pnl:+,.0f}원 ({pct:+.2f}%)")
    lines.append("— 해외 —")
    if ov is None:
        lines.append("조회 실패 (rate limit/네트워크)")
    elif not ov.positions:
        lines.append("보유 없음")
    else:
        for p in ov.positions:
            pnl_amt = (p["eval_amt"] - p["avg_price"] * p["qty"]) / 100
            lines.append(
                f"`{p['ticker']}` {p['name']} {p['qty']}주 "
                f"@{p['avg_price'] / 100:,.2f} → {p['current_price'] / 100:,.2f} "
                f"(${pnl_amt:+,.2f}, {p['pnl_pct']:+.2f}%)"
            )
        _, _, pnl, pct = _pnl_totals(ov.positions, scale=100)
        lines.append(f"  └ 해외 합계: ${pnl:+,.2f} ({pct:+.2f}%)")
    return "\n".join(lines)


_TELEGRAM_MSG_LIMIT = 4096


def _history_text(trades, summary) -> str:
    """종목명 길이는 자유 변수라 행 수 clamp만으로는 4096자 제한을 보장할 수 없다.
    그래서 렌더 길이로 예산을 잡고, 넘칠 다음 항목에서 멈춘 뒤 몇 건이 잘렸는지
    안내한다. 요약 블록(summary)은 항상 clamp된 전체 구간 기준이며 절대 자르지 않는다."""
    if not trades:
        return "*📜 거래 내역*\n청산된 거래가 아직 없어."

    footer = "\n".join(
        [
            "─────",
            f"{summary.trades}건 · 승 {summary.wins} / 패 {summary.losses} "
            f"(승률 {summary.win_rate_pct:.1f}%)",
            f"누적손익 *{summary.total_pnl_won:+,}원*",
        ]
    )
    header = f"*📜 거래 내역* (최근 {len(trades)}건)"

    body: list[str] = []
    shown = 0
    for t in trades:
        date_str = t.exit_at.strftime("%m/%d") if t.exit_at else "-"
        reason = t.exit_reason.lower().replace("_", "-") if t.exit_reason else "-"
        entry = (
            f"`{t.ticker}` {t.name} {t.qty}주\n"
            f"  {t.entry_price:,} → {t.exit_price:,}  {t.pnl_won:+,}원 "
            f"({t.pnl_pct:+.2f}%)  {reason}  {date_str}"
        )
        remaining_after = len(trades) - shown - 1
        notice = f"… 외 {remaining_after}건 (길이 제한)" if remaining_after > 0 else None
        candidate = [header, *body, entry, *([notice] if notice else []), footer]
        if len("\n".join(candidate)) > _TELEGRAM_MSG_LIMIT:
            break
        body.append(entry)
        shown += 1

    parts = [header, *body]
    if shown < len(trades):
        parts.append(f"… 외 {len(trades) - shown}건 (길이 제한)")
    parts.append(footer)
    return "\n".join(parts)


# ── 에이전트 ────────────────────────────────────────────────
class Agent:
    def __init__(self, tg: Telegram):
        self.tg = tg
        self.repo = Repo()
        self.pending: dict[str, dict] = {}
        self._token_seq = 0
        self.mode = _load_mode()

    def _client(self) -> KisClient:
        return KisClient(mode=self.mode)

    def _snapshot(self):
        """(domestic Balance|None, overseas Balance|None)."""
        c = self._client()
        with c.session() as s:
            dom = s.get_balance()
            time.sleep(1.1)  # KIS 1req/sec rate limit 회피
            try:
                ov = s.get_overseas_balance(exchange="NASD", currency="USD")
            except Exception as e:
                log.info("해외 잔고 조회 skip: %s", e)
                ov = None
        return dom, ov

    # ── 명령 처리 ──
    def handle_text(self, text: str) -> None:
        text = text.strip()
        low = text.lower()
        _audit("command", text=text)

        if low in ("/help", "/start", "도움말"):
            self.tg.send(
                "*🤖 트레이딩 에이전트*\n"
                "/balance 잔고 · /positions 포지션 · /status 현황 · /buyable 해외매수가능\n"
                "/history [N] 거래 내역 (기본 10건, 최대 50)\n"
                "/buy <코드> <수량> · /sell <코드> <수량>\n"
                "/mode paper|real 모드 전환 (현재: *" + self.mode.upper() + "*)\n"
                "또는 그냥 질문/지시를 자연어로 보내면 분석·제안해줘.\n"
                "_모든 주문은 확인 버튼을 눌러야 실행돼._"
            )
            return
        if low in ("/balance", "/잔고", "잔고"):
            self._cmd_balance(); return
        if low in ("/positions", "/포지션", "포지션"):
            self._cmd_positions(); return
        if low in ("/status", "/현황", "현황"):
            self._cmd_status(); return
        if low in ("/buyable", "/매수가능", "매수가능"):
            self._cmd_buyable(); return
        if low.startswith("/history") or low.startswith("/내역") or low == "내역":
            self._cmd_history(text); return
        if low.startswith("/mode") or low.startswith("모드"):
            self._cmd_mode(text); return
        if low.startswith("/buy") or low.startswith("/sell"):
            self._cmd_order(text); return

        # 자연어 → LLM
        self._cmd_nl(text)

    def _cmd_balance(self) -> None:
        try:
            dom, ov = self._snapshot()
            self.tg.send(_balance_text(dom, ov))
        except Exception as e:
            log.warning("balance 오류: %s", e)
            self.tg.send(f"⚠️ 잔고 조회 실패: {e}")

    def _cmd_positions(self) -> None:
        try:
            dom, ov = self._snapshot()
            self.tg.send(_positions_text(dom, ov))
        except Exception as e:
            log.warning("positions 오류: %s", e)
            self.tg.send(f"⚠️ 포지션 조회 실패: {e}")

    def _cmd_status(self) -> None:
        try:
            dom, ov = self._snapshot()
            active = len(self.repo.get_active_positions())
            today = self.repo.get_today_entries()
            txt = _balance_text(dom, ov)
            # 전체 투자금액 대비 손익 (±금액/±%)
            if dom and dom.positions:
                inv, cur, pnl, pct = _pnl_totals(dom.positions)
                txt += (f"\n\n*국내 손익*: 매입 {_fmt_won(inv)} → 평가 {_fmt_won(cur)}\n"
                        f"  {pnl:+,.0f}원 ({pct:+.2f}%)")
            if ov and ov.positions:
                inv, cur, pnl, pct = _pnl_totals(ov.positions, scale=100)
                txt += (f"\n*해외 손익*: 매입 ${inv:,.2f} → 평가 ${cur:,.2f}\n"
                        f"  ${pnl:+,.2f} ({pct:+.2f}%)")
            txt += f"\n\n*계좌(DB)*: 활성 포지션 {active} · 오늘 진입 {today}"
            self.tg.send(txt)
        except Exception as e:
            log.warning("status 오류: %s", e)
            self.tg.send(f"⚠️ 현황 조회 실패: {e}")

    def _cmd_buyable(self) -> None:
        try:
            c = self._client()
            with c.session() as s:
                bq = s.get_overseas_buyable("AAPL", 200.0, "NASD")
            if not bq:
                self.tg.send("⚠️ 해외 매수가능금액 조회 실패")
                return
            self.tg.send(
                "*💵 해외 매수가능금액 (통합증거금 반영)*\n"
                f"외화 주문가능: ${bq.get('ord_psbl_frcr_amt', '-')}\n"
                f"해외 주문가능: ${bq.get('ovrs_ord_psbl_amt', '-')}\n"
                f"환전후 주문가능: ${bq.get('echm_af_ord_psbl_amt', '-')}\n"
                f"환율: {bq.get('exrt', '-')}\n"
                "_0이면 해외 예수금(USD) 충전/활성화 필요._"
            )
        except Exception as e:
            self.tg.send(f"⚠️ 매수가능 조회 실패: {e}")

    def _cmd_history(self, text: str) -> None:
        parts = text.split()
        limit = 10
        if len(parts) > 1:
            try:
                limit = int(parts[1])
            except ValueError:
                self.tg.send("형식: `/history` 또는 `/history 20` (최대 50건)")
                return
        try:
            trades = self.repo.get_closed_positions(limit)
            summary = self.repo.get_history_summary(limit)
            self.tg.send(_history_text(trades, summary))
        except Exception as e:
            log.warning("history 오류: %s", e)
            self.tg.send(f"⚠️ 거래 내역 조회 실패: {e}")

    def _cmd_mode(self, text: str) -> None:
        parts = text.split()
        arg = parts[1].lower() if len(parts) > 1 else ""
        if arg not in ("paper", "real"):
            self.tg.send(
                f"현재 모드: *{self.mode.upper()}*\n"
                "전환: `/mode paper` 또는 `/mode real`\n"
                "_real은 실거래(실제 돈)·실제 계좌._"
            )
            return
        if arg == self.mode:
            self.tg.send(f"이미 *{arg.upper()}* 모드야.")
            return
        if arg == "paper":
            self._switch_mode("paper")
            return
        # real 전환은 실거래라 버튼 확인
        kb = {"inline_keyboard": [[
            {"text": "🛑 REAL 실거래로 전환", "callback_data": "mode:real"},
            {"text": "❌ 취소", "callback_data": "mode:cancel"},
        ]]}
        self.tg.send(
            "🛑 *REAL(실거래) 전환 확인*\n"
            "전환하면 이후 모든 주문이 **실제 계좌·실제 돈**으로 체결돼.\n"
            "정말 전환할까?",
            reply_markup=kb,
        )

    def _switch_mode(self, mode: str) -> None:
        prev = self.mode
        if not _force_load_keys(mode):
            self.tg.send(
                f"⚠️ {mode.upper()} 키를 keychain에서 못 찾음 "
                f"(kis-openapi / {mode}-appkey·secret·account 확인). 모드 미변경."
            )
            _force_load_keys(prev)
            return
        try:
            c = KisClient(mode=mode)
            with c.session() as s:
                bal = s.get_balance()
        except Exception as e:
            self.tg.send(f"⚠️ {mode.upper()} 계좌 검증 실패: {e}. 모드 미변경.")
            _force_load_keys(prev)
            return
        if bal is None:
            self.tg.send(f"⚠️ {mode.upper()} 잔고 조회 실패 — 키/계좌 확인. 모드 미변경.")
            _force_load_keys(prev)
            return
        self.mode = mode
        _save_mode(mode)
        tag = "🛑 REAL(실거래)" if mode == "real" else "🧪 PAPER(모의)"
        self.tg.send(
            f"✅ 모드 전환 완료 → *{tag}*\n"
            f"예수금 {_fmt_won(bal.cash)} / 총평가 {_fmt_won(bal.total_eval)}"
        )
        _audit("mode_switch", mode=mode, prev=prev)

    def _cmd_order(self, text: str) -> None:
        parts = text.split()
        if len(parts) < 3:
            self.tg.send("형식: `/buy 005930 10` 또는 `/sell 005930 10`")
            return
        side = "buy" if parts[0].lower().startswith("/buy") else "sell"
        ticker = parts[1].strip()
        try:
            qty = int(parts[2])
        except ValueError:
            self.tg.send("수량은 정수여야 해.")
            return
        self._propose_order(side, ticker, qty)

    def _propose_order(self, side: str, ticker: str, qty: int, reason: str = "") -> None:
        if not (ticker.isdigit() and len(ticker) == 6):
            self.tg.send(f"⚠️ 국내 6자리 종목코드만 지원해 (받은 값: `{ticker}`). 해외 주문은 다음 단계.")
            return
        if qty <= 0:
            self.tg.send("⚠️ 수량은 1 이상이어야 해."); return

        c = self._client()
        try:
            with c.session() as s:
                q = s.get_quote(ticker)
        except Exception as e:
            self.tg.send(f"⚠️ 시세 조회 실패: {e}"); return
        if not q or q.current_price <= 0:
            self.tg.send(f"⚠️ `{ticker}` 현재가 조회 실패 — 주문 보류."); return

        price = q.current_price
        notional = price * qty
        if notional > MAX_ORDER_NOTIONAL_KRW:
            self.tg.send(
                f"🛑 주문 명목금액 {_fmt_won(notional)} > 상한 {_fmt_won(MAX_ORDER_NOTIONAL_KRW)} — 거부.\n"
                f"수량을 줄여줘."
            )
            _audit("order_rejected_cap", side=side, ticker=ticker, qty=qty, notional=notional)
            return

        self._token_seq += 1
        token = str(self._token_seq)
        self.pending[token] = {
            "side": side, "ticker": ticker, "qty": qty, "price": price,
            "created": time.time(),
        }
        verb = "매수" if side == "buy" else "매도"
        tag = "🛑REAL " if self.mode == "real" else ""
        msg = (
            f"*🧾 {tag}{verb} 확인*\n"
            f"`{ticker}` {qty}주 @ 지정가 {_fmt_won(price)}\n"
            f"명목 {_fmt_won(notional)} (현재가 {q.d_change_pct:+.2f}%)\n"
        )
        if reason:
            msg += f"_사유: {reason}_\n"
        msg += f"아래 버튼으로 확인/취소 (*{self.mode.upper()}*)."
        kb = {"inline_keyboard": [[
            {"text": f"✅ {tag}{verb} 실행", "callback_data": f"confirm:{token}"},
            {"text": "❌ 취소", "callback_data": f"cancel:{token}"},
        ]]}
        self.tg.send(msg, reply_markup=kb)
        _audit("order_proposed", token=token, side=side, ticker=ticker, qty=qty, price=price)

    def handle_callback(self, data: str, callback_id: str) -> None:
        if data.startswith("mode:"):
            self.tg.answer_callback(callback_id)
            if data.split(":", 1)[1] == "real":
                self._switch_mode("real")
            else:
                self.tg.send("모드 전환 취소.")
            return
        action, _, token = data.partition(":")
        order = self.pending.pop(token, None)
        if not order:
            self.tg.answer_callback(callback_id, "만료되었거나 알 수 없는 요청")
            self.tg.send("⚠️ 만료된 요청이야. 다시 시도해줘.")
            return
        if action == "cancel":
            self.tg.answer_callback(callback_id, "취소됨")
            self.tg.send(f"❌ 취소: {order['side']} {order['ticker']} {order['qty']}주")
            _audit("order_cancelled", **order)
            return
        if time.time() - order["created"] > PENDING_TTL_SEC:
            self.tg.answer_callback(callback_id, "만료됨")
            self.tg.send("⚠️ 확인 시간 초과 — 주문 취소됨.")
            return
        # 실행
        self.tg.answer_callback(callback_id, "실행 중…")
        self._execute(order)

    def _execute(self, order: dict) -> None:
        side, ticker, qty, price = order["side"], order["ticker"], order["qty"], order["price"]
        c = self._client()
        try:
            with c.session() as s:
                if side == "buy":
                    res = s.submit_buy(ticker, qty, price)
                else:
                    res = s.submit_sell(ticker, qty, price)
        except Exception as e:
            log.warning("주문 실행 예외: %s", e)
            self.tg.send(f"⚠️ 주문 실행 오류: {e}")
            _audit("order_error", error=str(e), **order)
            return
        if res.accepted:
            verb = "매수" if side == "buy" else "매도"
            self.tg.send(f"✅ {verb} 접수: `{ticker}` {qty}주 @ {_fmt_won(price)}\n주문번호 {res.broker_order_id}")
            _audit("order_executed", broker_order_id=res.broker_order_id, **order)
        else:
            msg1 = res.raw.get("msg1") or res.raw.get("error") or res.raw
            self.tg.send(f"⚠️ 주문 거부: {msg1}")
            _audit("order_rejected_broker", reason=str(msg1), **order)

    def _cmd_nl(self, text: str) -> None:
        try:
            dom, _ = self._snapshot()
        except Exception as e:
            dom = None
            log.info("NL snapshot skip: %s", e)
        pos_brief = ", ".join(
            f"{p['ticker']}({p['name']}) {p['qty']}주 {p['pnl_pct']:+.1f}%"
            for p in (dom.positions if dom else [])
        ) or "없음"
        cash = dom.cash if dom else 0
        prompt = (
            "너는 한국투자증권 모의(paper)계좌의 트레이딩 보조다. 사용자의 한국어 메시지를 해석해 "
            "정확히 아래 JSON 한 개만 출력한다(코드블록·설명 금지).\n"
            '{"action":"ANALYZE|BUY|SELL|NONE","ticker":null,"qty":null,"message":"한국어 답변"}\n'
            "- 분석/질문/조회 의도면 action=ANALYZE, message에 분석·답변 전문.\n"
            "- 매수/매도 의도가 명확하고 6자리 종목코드와 수량이 분명할 때만 BUY/SELL, ticker/qty 채우고 message에 한줄 사유.\n"
            "- 코드/수량이 불명확하면 ANALYZE로 두고 message에 무엇이 필요한지 묻는다.\n"
            f"[계좌] 예수금 {cash:,}원, 보유: {pos_brief}\n"
            f"[사용자] {text}"
        )
        out, src, _ = call_llm(prompt, timeout=LLM_TIMEOUT_SEC)
        if src == "unavailable":
            self.tg.send("⚠️ LLM 호출 실패 (claude/pi 모두). 잠시 후 다시 시도해줘.")
            return
        action = self._parse_action(out)
        if not action:
            self.tg.send(out[:3500])  # JSON 파싱 실패 → 원문 전달
            return
        act = (action.get("action") or "NONE").upper()
        message = action.get("message") or ""
        if act in ("BUY", "SELL") and action.get("ticker") and action.get("qty"):
            try:
                qty = int(action["qty"])
            except (ValueError, TypeError):
                qty = 0
            self._propose_order(act.lower(), str(action["ticker"]).strip(), qty, reason=message)
        else:
            self.tg.send(message[:3500] or "무엇을 도와줄까?")

    @staticmethod
    def _parse_action(out: str) -> dict | None:
        s = out.find("{")
        e = out.rfind("}")
        if s == -1 or e == -1 or e <= s:
            return None
        try:
            return json.loads(out[s:e + 1])
        except json.JSONDecodeError:
            return None


# ── 메인 루프 ────────────────────────────────────────────────
def run() -> int:
    mode0 = _load_mode()
    _force_load_keys(mode0)
    keys = load_telegram_keys()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN/CHAT_ID 미설정 (keychain): %s", keys)
        return 1

    tg = Telegram(token, chat_id)
    me = tg.get_me()
    log.info("봇 시작: @%s (id=%s) authorized_chat=%s mode=%s",
             me.get("username"), me.get("id"), chat_id, mode0)
    tg.set_commands()
    tag = "🛑 REAL(실거래)" if mode0 == "real" else "🧪 PAPER(모의)"
    tg.send(f"🤖 트레이딩 에이전트 온라인 (@{me.get('username')}) · 모드 *{tag}*. /help")

    agent = Agent(tg)
    # 시작 시 잔고 스냅샷 진단 (국내/해외 보유 상태를 로그로 즉시 확인)
    try:
        d0, o0 = agent._snapshot()
        nd = len(d0.positions) if d0 else "fail"
        if o0 is None:
            no = "fail(None)"
        else:
            no = f"{len(o0.positions)} (cash={o0.cash})"
        log.info("startup snapshot: domestic=%s, overseas=%s", nd, no)
    except Exception as e:
        log.warning("startup snapshot 실패: %s", e)

    offset = 0
    while True:
        updates = tg.get_updates(offset)
        for u in updates:
            offset = u["update_id"] + 1
            # 인가 chat_id만
            msg = u.get("message")
            cb = u.get("callback_query")
            if msg:
                frm = str(msg.get("chat", {}).get("id"))
                if frm != str(chat_id):
                    log.warning("미인가 chat %s 무시", frm)
                    _audit("unauthorized", chat=frm, text=msg.get("text"))
                    continue
                t = msg.get("text")
                if t:
                    try:
                        agent.handle_text(t)
                    except Exception as e:
                        log.exception("handle_text 오류")
                        tg.send(f"⚠️ 처리 오류: {e}")
            elif cb:
                frm = str(cb.get("from", {}).get("id"))
                if frm != str(chat_id):
                    tg.answer_callback(cb.get("id", ""), "미인가")
                    continue
                try:
                    agent.handle_callback(cb.get("data", ""), cb.get("id", ""))
                except Exception as e:
                    log.exception("handle_callback 오류")
                    tg.send(f"⚠️ 콜백 오류: {e}")


if __name__ == "__main__":
    sys.exit(run())
