"""호가 단위 + 수량 계산 테스트."""
from __future__ import annotations

import pytest

from src.broker.order_translator import (
    calc_qty,
    calc_risk_based_qty,
    calc_stop_loss_price,
    calc_take_profit_price,
    round_to_tick,
    tick_size,
)


# 정상: 가격대별 tick_size
@pytest.mark.parametrize("price,expected", [
    (1_500, 1),
    (3_000, 5),
    (15_000, 10),
    (35_000, 50),
    (142_500, 100),
    (350_000, 500),
    (1_000_000, 1_000),
])
def test_tick_size(price, expected):
    assert tick_size(price) == expected


# 정상: round_to_tick 표준 라운딩
def test_round_to_tick_round():
    assert round_to_tick(142_530) == 142_500
    assert round_to_tick(142_570) == 142_600


# 경계값: floor 모드 (매수 보수)
def test_round_to_tick_floor():
    assert round_to_tick(142_590, mode="floor") == 142_500


# 경계값: ceil 모드 (매도 보수)
def test_round_to_tick_ceil():
    assert round_to_tick(142_510, mode="ceil") == 142_600


# 정상: 수량 계산
def test_calc_qty_normal():
    # 자본 100만원 × 3% = 3만원. 종목 1만원 → 3주
    assert calc_qty(price=10_000, capital_won=1_000_000, size_pct=3.0) == 3


# 경계값: 자본 부족으로 0주
def test_calc_qty_too_expensive_returns_zero():
    # 자본 100만원 × 3% = 3만원. 종목 5만원 → 0주
    assert calc_qty(price=50_000, capital_won=1_000_000, size_pct=3.0) == 0


# 경계값: 입력 0
def test_calc_qty_zero_inputs():
    assert calc_qty(0, 1_000_000, 3.0) == 0
    assert calc_qty(10_000, 0, 3.0) == 0
    assert calc_qty(10_000, 1_000_000, 0.0) == 0


# 경계값: 음수 입력
def test_calc_qty_negative_safe():
    assert calc_qty(-100, 1_000_000, 3.0) == 0


# 정상: 계좌 리스크 기반 수량 계산
def test_calc_risk_based_qty_limits_loss_to_budget():
    # 자본 1,000,000원 × 0.25% = 2,500원 리스크 예산.
    # 1주 리스크 500원 → 5주.
    assert calc_risk_based_qty(10_000, 9_500, 1_000_000, 0.25) == 5


# 에러/경계값: 손절가가 진입가 이상이면 리스크 계산 불가
@pytest.mark.parametrize("entry,stop", [(10_000, 10_000), (10_000, 10_100), (0, 9_500)])
def test_calc_risk_based_qty_invalid_inputs(entry, stop):
    assert calc_risk_based_qty(entry, stop, 1_000_000, 0.25) == 0


# 정상: 손절가 — 호가 단위 보정
def test_stop_loss_price_rounds_up():
    # 142,500 × -2% = 139,650 → 호가 단위 100원 ceil → 139,700
    assert calc_stop_loss_price(142_500, 2.0) == 139_700


# 정상: 익절가 — 호가 단위 round
def test_take_profit_price_rounds():
    # 142,500 × +3% = 146,775 → 100원 round → 146,800
    assert calc_take_profit_price(142_500, 3.0) == 146_800


# 사이드이펙트: 저가 종목 호가 단위 자동 적응
def test_low_price_tick_adaptation():
    # 1,500원 × 2% = 30원, tick 1원
    assert calc_stop_loss_price(1_500, 2.0) == 1_470
    # 1,500원 × 3% = 1,545원 → tick 1원
    assert calc_take_profit_price(1_500, 3.0) == 1_545

from src.broker.order_translator import (
    calc_stop_loss_minor,
    calc_take_profit_minor,
    format_minor_price,
    round_to_minor_unit,
)


def test_overseas_minor_price_helpers():
    assert round_to_minor_unit(19_564.7, mode="floor") == 19_564
    assert format_minor_price(19_564, scale=100) == "195.64"
    assert calc_stop_loss_minor(20_000, 2.5) == 19_500
    assert calc_take_profit_minor(20_000, 6.0) == 21_200
