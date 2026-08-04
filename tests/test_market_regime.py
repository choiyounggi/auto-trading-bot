"""시장 레짐 게이트 — 상향장 추격 모드 테스트."""
from __future__ import annotations

import pytest

from src.guardrails.clamp import validate_market_regime
from src.guardrails.rules import TradingRules


@pytest.fixture
def rules() -> TradingRules:
    return TradingRules()


def _macro(kospi_d=0.5, kospi_w=2.0, kosdaq_w=2.0, vix_close=15.0) -> list[dict]:
    return [
        {"label": "코스피", "d_change_pct": kospi_d, "w_change_pct": kospi_w, "close": 2700.0},
        {"label": "코스닥", "d_change_pct": 0.5, "w_change_pct": kosdaq_w, "close": 850.0},
        {"label": "VIX(공포지수)", "d_change_pct": 0.0, "w_change_pct": 0.0, "close": vix_close},
    ]


# 정상: 상향장 통과
def test_bull_market_allows(rules):
    ok, reason = validate_market_regime(_macro(kospi_d=1.2, kospi_w=3.0, kosdaq_w=4.0), rules)
    assert ok
    assert reason is None


# 경계값: KOSPI 5일 -1.9% (임계 -2.0% 위) → 통과
def test_kospi_5d_at_edge_allows(rules):
    ok, reason = validate_market_regime(_macro(kospi_w=-1.9), rules)
    assert ok


# 차단: KOSPI 5일 -2.5% → 약세장 차단
def test_kospi_5d_below_threshold_blocks(rules):
    ok, reason = validate_market_regime(_macro(kospi_w=-2.5), rules)
    assert not ok
    assert reason is not None
    assert "KOSPI 5일" in reason


# 차단: KOSDAQ 5일 -5% → 약세장 차단
def test_kosdaq_5d_below_threshold_blocks(rules):
    ok, reason = validate_market_regime(_macro(kosdaq_w=-5.0), rules)
    assert not ok
    assert "KOSDAQ 5일" in reason


# 차단: VIX 30 → 위험 회피
def test_vix_above_threshold_blocks(rules):
    ok, reason = validate_market_regime(_macro(vix_close=30.0), rules)
    assert not ok
    assert "VIX" in reason


# 차단: KOSPI 당일 -3% → panic day
def test_kospi_1d_panic_blocks(rules):
    ok, reason = validate_market_regime(_macro(kospi_d=-3.0), rules)
    assert not ok
    assert "panic day" in reason


# 경계값: 매크로 데이터 빈 배열 → 통과 (필터 안 함)
def test_empty_macro_allows(rules):
    ok, reason = validate_market_regime([], rules)
    assert ok


# 경계값: 라벨 누락 (코스피 없음) → 다른 조건만 평가
def test_partial_macro_kospi_missing(rules):
    partial = [
        {"label": "코스닥", "d_change_pct": 0.0, "w_change_pct": 1.0, "close": 850.0},
        {"label": "VIX(공포지수)", "close": 15.0},
    ]
    ok, _ = validate_market_regime(partial, rules)
    assert ok


# 사이드이펙트: 첫 위반 조건만 reason에 반영 (KOSPI 5일 + KOSDAQ 5일 동시 차단 시 KOSPI 우선)
def test_first_violation_wins(rules):
    ok, reason = validate_market_regime(_macro(kospi_w=-10.0, kosdaq_w=-10.0), rules)
    assert not ok
    assert "KOSPI 5일" in reason


# 사이드이펙트: yaml override 임계 변경 시 동작
def test_custom_threshold(rules):
    strict = TradingRules(block_if_kospi_5d_pct_below=0.0)  # 0% 미만 차단 (즉 상향만)
    ok, _ = validate_market_regime(_macro(kospi_w=-0.5), strict)
    assert not ok  # 0% 미만이라 차단

    ok, _ = validate_market_regime(_macro(kospi_w=0.5), strict)
    assert ok  # 양수 통과
