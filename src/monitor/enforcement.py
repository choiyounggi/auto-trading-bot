"""결정론적 강제 청산 룰 — LLM과 무관하게 작동.

- 손절가 도달 → STOP_LOSS 청산
- 익절가 도달 → TAKE_PROFIT 청산 (부분 익절 조건 충족 시 일부만 매도 후 TP 연장)
- 시간 stop (max_hold_until 도달) → TIME_STOP
- Trailing stop 활성 시 → 고점 - ATR×1.5 이탈 → TRAILING_STOP
- 일일 손실 한도 도달 → DAILY_LIMIT (모든 OPEN 청산)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class ExitTrigger:
    ticker: str
    reason: str  # STOP_LOSS / TAKE_PROFIT / TIME_STOP / TRAILING_STOP / DAILY_LIMIT
    exit_price: int


@dataclass
class PartialExitPlan:
    sell_qty: int
    remain_qty: int
    new_take_profit: int
    new_stop_loss: int | None  # None = 손절가 유지


def held_qty(position) -> int:
    """부분 매도 이력 반영한 현재 보유 수량."""
    remaining = getattr(position, "qty_remaining", None)
    return remaining if remaining is not None else (position.entry_qty or 0)


def plan_partial_take_profit(position, rules) -> PartialExitPlan | None:
    """TAKE_PROFIT 트리거 시 부분 익절 계획. None이면 기존대로 전량 청산.

    1차 익절가 도달 시 sell_pct만큼만 매도해 실익을 확정하고,
    잔여분 TP는 measured-move 연장(tp + (tp-entry)×extension),
    손절가는 본전(진입가)으로 상향해 잔여분 최악 케이스도 순이익을 보장한다.
    부분 익절은 포지션당 1회 — 연장 TP 재도달 시엔 전량 청산.
    """
    if not getattr(rules, "partial_tp_enabled", False):
        return None
    if (getattr(position, "partial_exit_count", 0) or 0) >= 1:
        return None

    entry = position.entry_price_actual or position.entry_price_target or 0
    tp = position.current_take_profit or 0
    held = held_qty(position)
    if entry <= 0 or tp <= entry or held < 2:
        return None

    sell = int(held * float(rules.partial_tp_sell_pct) / 100.0)
    if sell < 1 or held - sell < 1:
        return None

    new_tp = tp + int((tp - entry) * float(rules.partial_tp_extension))
    if new_tp <= tp:
        return None

    new_sl = entry if getattr(rules, "partial_tp_breakeven_stop", False) else None
    return PartialExitPlan(
        sell_qty=sell,
        remain_qty=held - sell,
        new_take_profit=new_tp,
        new_stop_loss=new_sl,
    )


def check_stop_loss(position, current_price: int) -> ExitTrigger | None:
    if position.current_stop_loss is None:
        return None
    if current_price <= position.current_stop_loss:
        return ExitTrigger(position.ticker, "STOP_LOSS", current_price)
    return None


def check_take_profit(position, current_price: int) -> ExitTrigger | None:
    if position.current_take_profit is None:
        return None
    if current_price >= position.current_take_profit:
        return ExitTrigger(position.ticker, "TAKE_PROFIT", current_price)
    return None


def check_time_stop(position, today: date | None = None) -> ExitTrigger | None:
    if position.max_hold_until is None:
        return None
    today = today or date.today()
    if today >= position.max_hold_until:
        return ExitTrigger(position.ticker, "TIME_STOP", 0)  # 시장가 → 호출 측에서 가격 결정
    return None


def check_trailing(position, current_price: int, atr: float) -> ExitTrigger | None:
    if not position.trailing_active:
        return None
    high = max(position.trailing_high or 0, current_price)
    trail_stop = high - int(atr * 1.5)
    if current_price <= trail_stop:
        return ExitTrigger(position.ticker, "TRAILING_STOP", current_price)
    return None


def evaluate_exits(position, current_price: int, atr: float) -> ExitTrigger | None:
    """우선순위: STOP_LOSS > TIME_STOP > TRAILING > TAKE_PROFIT."""
    for fn in (
        lambda: check_stop_loss(position, current_price),
        lambda: check_time_stop(position),
        lambda: check_trailing(position, current_price, atr),
        lambda: check_take_profit(position, current_price),
    ):
        trigger = fn()
        if trigger:
            return trigger
    return None
