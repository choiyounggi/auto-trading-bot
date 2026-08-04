"""지수 ETF 단계적 줍줍(dip-buy) — 코스피/코스닥이 많이 빠지면 단계별로 매수.

수급 신호와 무관한 독립 역발상 경로다. 큰 하락일엔 수급 후보가 0이라
orchestrator가 조기 종료하므로, 이 경로는 run() 상단에서 독립 실행된다.

순수 로직(compute_drop_pct / select_tranche)은 단위테스트로 검증하고,
run_dip_buy 만 KIS/DB IO를 다룬다.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

# KIS 모의투자 API는 초당 거래건수 제한이 있어 연속 호출 사이에 간격을 둔다.
_KIS_GAP_SEC = 1.1

# 정규장 시간이 아니라서 나는 거부(시간외/휴장/장종료)는 정상 상황이므로 경고하지 않는다.
# 잔고부족·종목오류 등 '진짜 문제' 거부만 텔레그램 경고로 올린다.
_MARKET_CLOSED_HINTS = (
    "장종료", "장 종료", "장개시", "장 개시", "장운영", "장 운영",
    "거래시간", "거래 시간", "영업일", "휴장", "장마감", "장 마감",
)


def is_market_closed_reject(msg: str) -> bool:
    """주문 거부 사유가 '정규장 시간 아님'(시간외/휴장/장종료)인지."""
    return any(h in (msg or "") for h in _MARKET_CLOSED_HINTS)

log = logging.getLogger("stock-trader.dip_buy")


def compute_drop_pct(closes: list[int], window: int) -> float | None:
    """과거→최근 종가 리스트에서 window일 누적 등락률(%). 데이터 부족 시 None."""
    if not closes or len(closes) <= window:
        return None
    base = closes[-1 - window]
    last = closes[-1]
    if base <= 0:
        return None
    return (last / base - 1.0) * 100.0


def select_tranche(
    drop_pct: float | None,
    tranches: list[dict],
    filled_count: int,
    max_exposure_pct: float,
) -> dict | None:
    """다음에 살 단계(tranche)를 고른다. 없으면 None.

    - tranches: [{"drop_pct": -3, "size_pct": 2}, ...] (낙폭 얕은→깊은 순으로 정렬)
    - filled_count: 해당 ETF의 이미 보유중(OPEN/PENDING) dip 포지션 수 = 채워진 단계 수
    규칙: 현재 낙폭이 충족하는 단계 수(qualified)가 filled_count보다 클 때만,
    다음 단계 1개를 산다(=하루 1회·중복차수 방지). 단, 누적 노출이 상한을 넘으면 중단.
    """
    if drop_pct is None:
        return None
    ts = sorted(tranches, key=lambda t: -float(t["drop_pct"]))  # 얕은(-3)→깊은(-10)
    if filled_count >= len(ts):
        return None
    qualified = sum(1 for t in ts if drop_pct <= float(t["drop_pct"]))
    if filled_count >= qualified:
        return None
    nxt = ts[filled_count]
    exposure_after = sum(float(t["size_pct"]) for t in ts[: filled_count + 1])
    if exposure_after > max_exposure_pct:
        return None
    return nxt


def run_dip_buy(
    client: Any,
    repo: Any,
    rules: Any,
    send_info: Callable[[str], Any],
    send_warning: Callable[[str], Any],
) -> int:
    """지수 ETF dip-buy 1회 평가/실행. 매수한 단계 수를 반환."""
    cfg = getattr(rules, "dip_buy", {}) or {}
    if not cfg.get("enabled"):
        return 0
    etfs: dict = cfg.get("index_etf", {}) or {}
    tranches: list = cfg.get("tranches", []) or []
    window = int(cfg.get("window_days", 5))
    max_exp = float(cfg.get("max_total_exposure_pct", 15.0))
    sl_pct = float(cfg.get("stop_loss_pct", 8.0))
    tp_pct = float(cfg.get("take_profit_pct", 5.0))
    max_hold = int(cfg.get("max_hold_days", 20))
    if not etfs or not tranches:
        return 0

    balance = client.get_balance()
    if balance is None:
        log.warning("dip-buy: 잔고 조회 실패 → skip")
        return 0
    cash = balance.cash

    bought = 0
    for idx_label, etf in etfs.items():
        etf = str(etf)
        time.sleep(_KIS_GAP_SEC)  # rate-limit 회피 (직전 KIS 호출과 간격)
        try:
            closes = client.get_daily_closes(etf, window + 2)
        except Exception as e:
            log.warning("dip-buy %s 일별시세 실패: %s", etf, e)
            continue
        drop = compute_drop_pct(closes, window)
        filled = repo.count_open_dip_positions(etf)
        tr = select_tranche(drop, tranches, filled, max_exp)
        log.info("dip-buy %s(%s): %dd=%s%% filled=%d → %s",
                 idx_label, etf, window, f"{drop:.2f}" if drop is not None else "n/a", filled, tr)
        if not tr:
            continue
        time.sleep(_KIS_GAP_SEC)
        q = client.get_quote(etf)
        if not q or q.current_price <= 0:
            log.warning("dip-buy %s 현재가 조회 실패 → skip", etf)
            continue
        price = int(q.current_price)
        # 낙폭이 도달한 미보유 단계를 한 번에 전부 채운다 (2026-07-06 영기 확정 —
        # 기존 '실행당 1단계'는 깊은 급락에도 첫 단계만 사서 낙폭 대비 과소 베팅).
        # select_tranche가 노출 상한(max_exp)·자격 단계 수를 계속 검사하므로 무한루프 없음.
        while tr:
            qty = int(cash * (float(tr["size_pct"]) / 100.0) / price)
            if qty <= 0:
                log.info("dip-buy %s: 수량 0 (cash=%d size=%s%%) → skip", etf, cash, tr["size_pct"])
                break
            time.sleep(_KIS_GAP_SEC)
            result = client.submit_buy(etf, qty, price)
            if not result.accepted:
                reason = str(result.raw.get("msg1", result.raw))
                if is_market_closed_reject(reason):
                    # 시간외/휴장 거부는 정상 — 경고 없이 조용히 skip(로그만).
                    log.info("dip-buy %s: 정규장 시간 아님으로 skip (%s)", etf, reason)
                else:
                    send_warning(f"줍줍 주문 거부 {etf}: {reason}")
                break
            stop_loss = int(price * (1 - sl_pct / 100.0))
            take_profit = int(price * (1 + tp_pct / 100.0))
            tranche_no = filled + 1
            pos_id = repo.insert_position(
                ticker=etf,
                name=f"DIP {idx_label.upper()} {etf}",
                signal_score=0,
                confidence=0,
                broker_order_id=result.broker_order_id,
                strategy="MARKET_OPEN",
                price_target=price,
                qty=qty,
                thesis=f"지수 {idx_label.upper()} {window}일 {drop:.1f}% 급락 단계줍줍 {tranche_no}차",
                watch_signals=[],
                stop_loss=stop_loss,
                take_profit=take_profit,
                max_hold_days=max_hold,
                strategy_id="dip_buy",
                strategy_score=0,
                features={"index": idx_label, "etf": etf, "drop_pct": drop, "tranche": tranche_no},
            )
            bought += 1
            send_info(
                f"📉 줍줍 매수 ({idx_label.upper()} {tranche_no}차)\n"
                f"{etf} {window}일 {drop:.1f}% 하락\n"
                f"{price:,}원 × {qty}주 = {price * qty:,}원 (예수금 {tr['size_pct']}%)\n"
                f"손절 {stop_loss:,} / 익절 {take_profit:,} · 포지션 {pos_id}"
            )
            filled += 1
            tr = select_tranche(drop, tranches, filled, max_exp)
    return bought
