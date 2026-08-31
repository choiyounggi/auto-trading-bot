"""position-monitor entrypoint — 정규장 매 30분 launchd fire (09:00~15:30).

흐름:
  1. Kill Switch 체크
  2. positions WHERE status=OPEN 조회
  3. KIS REST API 현재가 조회
  4. 결정론적 강제 청산 평가 (enforcement.evaluate_exits)
  5. 없으면 LLM 동적 결정 호출
  6. 손절/익절 정정 주문 또는 청산
  7. Telegram 알림
"""
from __future__ import annotations

import json
import logging
import sys
from functools import lru_cache

import requests
from datetime import date, datetime, time
from pathlib import Path

from src.broker.kis_client import KisClient
from src.guardrails.kill_switch import is_active as kill_active
from src.guardrails.rules import load_rules
from src.monitor.dynamic_decision import decide_monitor
from src.monitor.enforcement import evaluate_exits, held_qty, plan_partial_take_profit
from src.monitor.trailing import maybe_activate_trailing, update_trailing_high
from src.notify.order_reject import warn_order_reject
from src.notify.telegram import send_critical, send_info, send_warning
from src.storage.repository import Repo
from src.util.keychain import load_kis_keys, load_telegram_keys

load_kis_keys()
load_telegram_keys()

log = logging.getLogger("stock-trader.monitor")
Path("data/logs").mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("data/logs/monitor.log"),
        logging.StreamHandler(sys.stdout),
    ],
)

REGULAR_START = time(9, 0)
REGULAR_END = time(15, 30)
US_REGULAR_START_KST = time(22, 30)
US_REGULAR_END_KST = time(6, 0)


def _warn_reject(message: str) -> None:
    """주문 거부 경고 — 같은 문구는 하루 한 번만 나간다 (src/notify/order_reject.py)."""
    warn_order_reject(message, send_warning, date.today().isoformat())


def _is_kr_regular_session(now: datetime) -> bool:
    return REGULAR_START <= now.time() <= REGULAR_END and now.weekday() < 5


def _is_us_regular_session_kst(now: datetime) -> bool:
    # 대략적인 미국 정규장 KST: 22:30~06:00 (서머타임/휴장 미세 보정은 추후 캘린더화)
    t = now.time()
    return (t >= US_REGULAR_START_KST and now.weekday() < 5) or (
        t <= US_REGULAR_END_KST and now.weekday() in (1, 2, 3, 4, 5)
    )


def _position_features(pos) -> dict:
    try:
        return json.loads(pos.entry_features_json or "{}")
    except Exception:
        return {}


def _is_overseas_position(pos) -> bool:
    return _position_features(pos).get("asset_class") == "overseas_stock"


def _overseas_meta(pos) -> dict:
    f = _position_features(pos)
    return {
        "symbol": f.get("broker_symbol") or pos.ticker,
        "exchange": f.get("exchange") or "NASD",
        "quote_exchange": f.get("quote_exchange") or "NAS",
        "currency": f.get("currency") or "USD",
        "price_scale": int(f.get("price_scale") or 100),
    }


def _price_label(value: int, meta: dict | None = None) -> str:
    if meta:
        return f"{meta['currency']} {value / max(meta['price_scale'], 1):.2f}"
    return f"{value:,}원"


def _submit_sell_for_position(client: KisClient, pos, current_price: int, qty: int | None = None):
    sell_qty = qty if qty is not None else held_qty(pos)
    if _is_overseas_position(pos):
        meta = _overseas_meta(pos)
        sell_price = max(1, current_price - 2)  # 2 cent 아래 지정가
        order = client.submit_overseas_sell(
            meta["symbol"],
            sell_qty,
            sell_price,
            exchange=meta["exchange"],
            price_scale=meta["price_scale"],
        )
        return order, sell_price, _price_label(sell_price, meta)

    from src.broker.order_translator import round_to_tick, tick_size
    tick = tick_size(current_price)
    sell_price = round_to_tick(current_price - 2 * tick, mode="floor")
    order = client.submit_sell(pos.ticker, sell_qty, sell_price)
    return order, sell_price, _price_label(sell_price)


@lru_cache(maxsize=1)
def _fetch_vix_level() -> float:
    """Yahoo chart API로 VIX 근사 실시간 값을 가져온다. 실패 시 0."""
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?range=1d&interval=5m",
            timeout=5,
        )
        data = r.json()
        result = (data.get("chart", {}).get("result") or [{}])[0]
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        vals = [float(v) for v in closes if v is not None]
        return vals[-1] if vals else 0.0
    except Exception as e:
        log.info("VIX fetch 실패: %s", e)
        return 0.0


def _cached_overseas_quote(client: KisClient, cache: dict, symbol: str, quote_exchange: str, currency: str = "USD"):
    key = (symbol, quote_exchange, currency)
    if key not in cache:
        cache[key] = client.get_overseas_quote(symbol, quote_exchange=quote_exchange, currency=currency, price_scale=100)
    return cache[key]


def _overseas_market_exit_reason(client: KisClient, pos, quote, quote_cache: dict) -> str | None:
    """해외 전용 시장/갭다운 청산 트리거.

    - 개별 종목 gap down: 시가가 전일종가 대비 -3% 이하이고 현재도 약세
    - NASDAQ/SPY risk-off: QQQ와 SPY가 동시에 급락하고 포지션이 손실권
    - VIX risk-off: VIX 30 이상 + 지수 약세 + 포지션 손실권
    """
    meta = _overseas_meta(pos)
    entry = pos.entry_price_actual or pos.entry_price_target or 0
    pnl_pct = ((quote.current_price / entry - 1) * 100.0) if entry else 0.0

    if quote.prev_close and quote.today_open:
        gap_pct = (quote.today_open / quote.prev_close - 1.0) * 100.0
        if gap_pct <= -3.0 and quote.d_change_pct <= -3.0:
            return f"US_GAP_DOWN({gap_pct:+.1f}%, day={quote.d_change_pct:+.1f}%)"

    spy = _cached_overseas_quote(client, quote_cache, "SPY", "AMS")
    qqq = _cached_overseas_quote(client, quote_cache, "QQQ", "NAS")
    spy_d = spy.d_change_pct if spy else 0.0
    qqq_d = qqq.d_change_pct if qqq else 0.0
    if spy_d <= -1.5 and qqq_d <= -2.0 and pnl_pct <= 0.0:
        return f"US_INDEX_RISK(SPY={spy_d:+.1f}%, QQQ={qqq_d:+.1f}%)"

    vix = _fetch_vix_level()
    if vix >= 30.0 and min(spy_d, qqq_d) <= -1.0 and pnl_pct <= 0.0:
        return f"VIX_RISK_OFF(VIX={vix:.1f}, SPY={spy_d:+.1f}%, QQQ={qqq_d:+.1f}%)"

    return None


def run() -> int:
    now = datetime.now()

    kr_session = _is_kr_regular_session(now)
    us_session = _is_us_regular_session_kst(now)

    # launchd StartInterval=300은 24/7 fire — 국내/미국 정규장 외에는 필터
    if not (kr_session or us_session):
        return 0

    # 3. 공휴일 체크 (한국 공휴일 + 대체공휴일) — 국내 세션에만 적용
    try:
        import holidays
        kr_holidays = holidays.country_holidays("KR")
        if kr_session and now.date() in kr_holidays:
            log.info("공휴일 — 국내 세션 skip (%s)", kr_holidays.get(now.date()))
            kr_session = False
            if not us_session:
                return 0
    except ImportError:
        log.warning("holidays 패키지 없음 — 공휴일 체크 skip")

    # 4. PENDING 체결 동기화 + 보유 종목 체크
    repo = Repo()
    client = KisClient(mode="paper")
    pending = repo.get_pending_positions()
    if pending:
        with client.session() as c:
            balance = c.get_balance()
            broker_positions = list(balance.positions) if balance else []
            ov_balance = c.get_overseas_balance(exchange="NASD", currency="USD")
            if ov_balance:
                broker_positions.extend(ov_balance.positions)
        if balance is None:
            send_warning("PENDING 체결 동기화 실패: KIS 국내 잔고 조회 실패")
        else:
            filled, cancelled = repo.reconcile_pending_from_balance(broker_positions)
            if filled or cancelled:
                send_info(f"PENDING 동기화: OPEN {filled}건 / CANCELLED {cancelled}건")

    positions = repo.get_open_positions()
    if not positions:
        return 0

    # 5. Kill Switch 체크
    if kill_active():
        log.critical("Kill Switch 활성 — 강제 청산 모드 (LLM 호출 skip)")
        # TODO: 모든 OPEN 즉시 청산 로직 (KIS 호출 필요)

    # 6. LLM 호출 여부 — 30분 boundary 직후 5분 윈도우 내일 때만 (5분 polling에서 1/6 호출)
    llm_mode = (now.minute % 30) < 5

    rules = load_rules(Path("config/trading_rules.yaml"))

    log.info("monitor fire — %d건 보유, kr_session=%s, us_session=%s, llm_mode=%s, minute=%d",
             len(positions), kr_session, us_session, llm_mode, now.minute)
    overseas_quote_cache: dict = {}
    with client.session() as c:
        for pos in positions:
            overseas_meta = _overseas_meta(pos) if _is_overseas_position(pos) else None
            if overseas_meta and not us_session:
                continue
            if not overseas_meta and not kr_session:
                continue
            if overseas_meta:
                quote = c.get_overseas_quote(
                    overseas_meta["symbol"],
                    quote_exchange=overseas_meta["quote_exchange"],
                    currency=overseas_meta["currency"],
                    price_scale=overseas_meta["price_scale"],
                )
            else:
                quote = c.get_quote(pos.ticker)
            if quote is None:
                send_warning(f"{pos.ticker} 시세 조회 실패")
                continue

            # trailing high 갱신
            if pos.trailing_active and quote.current_price > (pos.trailing_high or 0):
                repo.update_trailing_high(pos.id, quote.current_price)
                pos.trailing_high = quote.current_price

            # 1. 해외 전용 시장/갭다운 리스크 청산 우선
            if overseas_meta:
                market_exit_reason = _overseas_market_exit_reason(c, pos, quote, overseas_quote_cache)
                if market_exit_reason:
                    log.warning("해외 시장 리스크 청산 트리거: %s reason=%s @ %d", pos.ticker, market_exit_reason, quote.current_price)
                    order, sell_price, sell_label = _submit_sell_for_position(c, pos, quote.current_price)
                    if order.accepted:
                        repo.close_position(pos.id, market_exit_reason, sell_price, order.broker_order_id)
                        send_info(
                            f"🔴 {pos.name} ({pos.ticker}) 해외 리스크 청산\n"
                            f"reason: {market_exit_reason}\n"
                            f"매도가: {sell_label}"
                        )
                    else:
                        _warn_reject(f"해외 리스크 청산 주문 거부 {pos.name}: {order.raw.get('msg1', '')}")
                    continue

            # 2. 결정론적 강제 청산 우선
            atr = max(quote.today_high - quote.today_low, int(quote.current_price * 0.01))
            exit_trigger = evaluate_exits(pos, quote.current_price, atr)
            if exit_trigger:
                # 2-a. TAKE_PROFIT 1차 도달 → 부분 익절 (조건 충족 시 일부만 매도, TP 연장)
                if exit_trigger.reason == "TAKE_PROFIT":
                    plan = plan_partial_take_profit(pos, rules)
                    if plan:
                        log.info("부분 익절 트리거: %s %d주 중 %d주 매도, 잔여 TP %d, SL %s",
                                 pos.ticker, held_qty(pos), plan.sell_qty,
                                 plan.new_take_profit, plan.new_stop_loss)
                        order, sell_price, sell_label = _submit_sell_for_position(
                            c, pos, quote.current_price, qty=plan.sell_qty)
                        if order.accepted:
                            repo.apply_partial_exit(
                                pos.id, plan.sell_qty, sell_price,
                                plan.new_take_profit, plan.new_stop_loss)
                            send_info(
                                f"🟢 {pos.name} ({pos.ticker}) 부분 익절 {plan.sell_qty}주 @ {sell_label}\n"
                                f"잔여 {plan.remain_qty}주 → 목표가 연장 {plan.new_take_profit:,}"
                                + (f", 손절 본전 상향 {plan.new_stop_loss:,}" if plan.new_stop_loss else "")
                            )
                        else:
                            _warn_reject(f"부분 익절 주문 거부 {pos.name}: {order.raw.get('msg1', '')}")
                        continue
                log.warning("강제 청산 트리거: %s reason=%s @ %d",
                            exit_trigger.ticker, exit_trigger.reason, quote.current_price)
                # 국내는 호가 -2틱, 해외는 2 cent 아래 지정가
                order, sell_price, sell_label = _submit_sell_for_position(c, pos, quote.current_price)
                if order.accepted:
                    repo.close_position(pos.id, exit_trigger.reason, sell_price, order.broker_order_id)
                    send_info(
                        f"🔴 {pos.name} ({pos.ticker}) 강제 청산\n"
                        f"reason: {exit_trigger.reason}\n"
                        f"매도가: {sell_label} (현재가 {_price_label(quote.current_price, overseas_meta) if overseas_meta else _price_label(quote.current_price)})"
                    )
                else:
                    _warn_reject(f"청산 주문 거부 {pos.name}: {order.raw.get('msg1', '')}")
                continue

            # llm_mode False면 enforcement만 수행, LLM 호출 skip
            if not llm_mode:
                continue

            # 2. LLM 동적 결정
            decision, violations, trace = decide_monitor(
                pos,
                current_price=quote.current_price,
                today_high=quote.today_high,
                today_low=quote.today_low,
                volume_ratio=1.0,  # TODO: 평소 거래량 비율 (KIS API 추가 호출 필요)
                macro_brief="(TODO 매크로 brief)",
                recent_news="(TODO 종목 최근 4h 뉴스)",
                rules=rules,
            )

            # LLM 결정 로깅
            try:
                repo.log_llm_decision(
                    position_id=pos.id,
                    decision_type="MONITOR",
                    model="claude-sonnet",
                    source=trace.get("source", ""),
                    response_text=trace.get("raw_text", ""),
                    response_json=None,
                    confidence=decision.confidence,
                    action=decision.action,
                    elapsed_ms=trace.get("elapsed_ms", 0),
                    parse_error=trace.get("parse_error"),
                )
            except Exception as e:
                log.warning("llm_decision 로깅 실패: %s", e)

            if violations:
                send_warning(f"{pos.name} 안전 규칙 위반: {', '.join(violations)}")

            if decision.action == "TIGHTEN_STOP" and decision.new_stop_loss:
                repo.update_stop_loss(pos.id, decision.new_stop_loss)
                send_info(
                    f"🔧 {pos.name} 손절 상향: {pos.current_stop_loss:,} → {decision.new_stop_loss:,}\n"
                    f"reason: {decision.reason}"
                )
            elif decision.action == "RAISE_TP" and decision.new_take_profit:
                repo.update_take_profit(pos.id, decision.new_take_profit)
                # tp_raised_count 임계 도달 시 trailing 활성
                if (pos.tp_raised_count or 0) + 1 >= rules.max_tp_raises:
                    repo.activate_trailing(pos.id)
                    send_info(f"{pos.name} trailing stop 자동 활성")
                send_info(
                    f"🔧 {pos.name} 익절 상향: {pos.current_take_profit:,} → {decision.new_take_profit:,}\n"
                    f"reason: {decision.reason}"
                )
            elif decision.action == "CLOSE_NOW":
                order, sell_price, sell_label = _submit_sell_for_position(c, pos, quote.current_price)
                if order.accepted:
                    repo.close_position(pos.id, "LLM_CLOSE", sell_price, order.broker_order_id)
                    send_warning(
                        f"🔴 {pos.name} LLM CLOSE_NOW\n"
                        f"reason: {decision.reason}\n"
                        f"매도가: {sell_label}"
                    )
                else:
                    _warn_reject(f"LLM_CLOSE 주문 거부 {pos.name}: {order.raw.get('msg1', '')}")

    return 0


if __name__ == "__main__":
    sys.exit(run())
