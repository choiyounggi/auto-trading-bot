"""호가 단위 자동 보정 + 수량 계산.

국내 주식은 원 단위 정수, 해외주식은 기존 SQLite 정수 가격 스키마와
호환되도록 minor unit(예: USD cent, price_scale=100) 정수로 처리한다.
"""
from __future__ import annotations

import math


def tick_size(price: int | float) -> int:
    """KRX 호가 단위 (가격대별)."""
    p = float(price)
    if p < 2_000:
        return 1
    if p < 5_000:
        return 5
    if p < 20_000:
        return 10
    if p < 50_000:
        return 50
    if p < 200_000:
        return 100
    if p < 500_000:
        return 500
    return 1_000


def round_to_tick(price: int | float, mode: str = "round") -> int:
    """
    호가 단위로 라운딩.
    mode: "round" / "floor" (매수 보수) / "ceil" (매도 보수)
    """
    tick = tick_size(price)
    p = float(price)
    if mode == "floor":
        return int(p // tick) * tick
    if mode == "ceil":
        return int(-(-p // tick)) * tick
    return int(round(p / tick)) * tick


def round_to_minor_unit(price: int | float, mode: str = "round", scale: int = 100) -> int:
    """이미 minor unit으로 정규화된 해외 가격을 정수 라운딩."""
    p = float(price)
    if mode == "floor":
        return int(math.floor(p))
    if mode == "ceil":
        return int(math.ceil(p))
    return int(round(p))

def format_minor_price(price_minor: int | float, scale: int = 100) -> str:
    """KIS 해외 주문용 가격 문자열."""
    if scale <= 1:
        return str(int(price_minor))
    return f"{float(price_minor) / scale:.2f}"


def calc_qty(price: int, capital_won: int, size_pct: float) -> int:
    """
    LLM 결정 size_pct(%) 기준 정수 수량.
    한국 주식은 소수점 매수 불가 → floor.
    """
    if price <= 0 or capital_won <= 0 or size_pct <= 0:
        return 0
    target_won = int(capital_won * size_pct / 100.0)
    qty = target_won // price
    return max(0, qty)


def calc_risk_based_qty(
    entry_price: int,
    stop_loss_price: int,
    capital_won: int,
    risk_per_trade_pct: float,
) -> int:
    """초기 손절가까지의 손실금액이 계좌 리스크 한도를 넘지 않도록 수량 계산."""
    per_share_risk = entry_price - stop_loss_price
    if entry_price <= 0 or stop_loss_price <= 0 or per_share_risk <= 0:
        return 0
    if capital_won <= 0 or risk_per_trade_pct <= 0:
        return 0
    risk_budget = int(capital_won * risk_per_trade_pct / 100.0)
    return max(0, risk_budget // per_share_risk)


def calc_stop_loss_price(entry_price: int, stop_loss_pct: float) -> int:
    """진입가 - %로 손절 절대가 계산. 호가 단위 floor (매도 시 약간 위)."""
    raw = entry_price * (1 - stop_loss_pct / 100.0)
    return round_to_tick(raw, mode="ceil")


def calc_take_profit_price(entry_price: int, take_profit_pct: float) -> int:
    """진입가 + %로 익절 절대가 계산. 호가 단위 round."""
    raw = entry_price * (1 + take_profit_pct / 100.0)
    return round_to_tick(raw, mode="round")


def calc_stop_loss_minor(entry_price: int, stop_loss_pct: float) -> int:
    """해외 minor unit 가격 기준 손절가. 1 cent 단위로 ceil."""
    return max(1, int(math.ceil(entry_price * (1 - stop_loss_pct / 100.0))))


def calc_take_profit_minor(entry_price: int, take_profit_pct: float) -> int:
    """해외 minor unit 가격 기준 익절가. 1 cent 단위로 round."""
    return max(1, int(round(entry_price * (1 + take_profit_pct / 100.0))))
