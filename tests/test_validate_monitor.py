"""동적 조정 결정 안전 규칙 테스트."""
from __future__ import annotations

import pytest

from src.guardrails.clamp import validate_monitor
from src.guardrails.rules import TradingRules
from src.llm.schemas import MonitorDecision


@pytest.fixture
def rules() -> TradingRules:
    return TradingRules()


# 정상: HOLD
def test_hold_passthrough(rules):
    d = MonitorDecision(action="HOLD", confidence=7, reason="안정")
    out, violations = validate_monitor(d, 140_000, 150_000, 145_000, 142_000, rules)
    assert out.action == "HOLD"
    assert violations == []


# 정상: TIGHTEN_STOP 상향 — 인정
def test_tighten_stop_upward_accepted(rules):
    d = MonitorDecision(action="TIGHTEN_STOP", new_stop_loss=143_000, confidence=8, reason="추세 약화")
    out, v = validate_monitor(d, 140_000, 150_000, 145_000, 142_000, rules)
    assert out.action == "TIGHTEN_STOP"
    assert out.new_stop_loss == 143_000
    assert v == []


# 핵심 안전 규칙: TIGHTEN_STOP 하향 시도 → 거부
def test_tighten_stop_downward_rejected(rules):
    d = MonitorDecision(action="TIGHTEN_STOP", new_stop_loss=138_000, confidence=8, reason="버티자")
    out, v = validate_monitor(d, 140_000, 150_000, 145_000, 142_000, rules)
    assert out.action == "HOLD"
    assert any("손절 하향" in s for s in v)


# 경계값: TIGHTEN_STOP인데 new_stop_loss 없음
def test_tighten_stop_without_price_rejected(rules):
    d = MonitorDecision(action="TIGHTEN_STOP", confidence=8, reason="")
    out, v = validate_monitor(d, 140_000, 150_000, 145_000, 142_000, rules)
    assert out.action == "HOLD"
    assert any("new_stop_loss" in s for s in v)


# 정상: RAISE_TP 상향 인정
def test_raise_tp_upward_accepted(rules):
    d = MonitorDecision(action="RAISE_TP", new_take_profit=152_000, confidence=8, reason="모멘텀")
    out, v = validate_monitor(d, 140_000, 150_000, 145_000, 142_000, rules)
    assert out.action == "RAISE_TP"
    assert out.new_take_profit == 152_000


# 안전 규칙: RAISE_TP 하향 시도 → 거부
def test_raise_tp_downward_rejected(rules):
    d = MonitorDecision(action="RAISE_TP", new_take_profit=148_000, confidence=8, reason="")
    out, v = validate_monitor(d, 140_000, 150_000, 145_000, 142_000, rules)
    assert out.action == "HOLD"
    assert any("상향 아님" in s for s in v)


# 안전 규칙: RAISE_TP가 현재가 이하 → 거부
def test_raise_tp_below_current_price_rejected(rules):
    d = MonitorDecision(action="RAISE_TP", new_take_profit=144_000, confidence=8, reason="")
    out, v = validate_monitor(d, 140_000, 150_000, 145_000, 142_000, rules)
    assert out.action == "HOLD"


# 안전 규칙: RAISE_TP entry × 1.10 절대 cap
def test_raise_tp_absolute_cap_applied(rules):
    entry = 100_000
    # 진입 +12% 시도 → +10%로 cap
    d = MonitorDecision(action="RAISE_TP", new_take_profit=112_000, confidence=8, reason="")
    out, v = validate_monitor(d, 95_000, 105_000, 108_000, entry, rules)
    assert out.action == "RAISE_TP"
    assert out.new_take_profit == 110_000
    assert any("절대 cap" in s for s in v)


# 정상: CLOSE_NOW with confidence >= 8
def test_close_now_high_confidence_accepted(rules):
    d = MonitorDecision(action="CLOSE_NOW", confidence=9, reason="thesis 깨짐")
    out, v = validate_monitor(d, 140_000, 150_000, 142_000, 142_000, rules)
    assert out.action == "CLOSE_NOW"


# 안전 규칙: CLOSE_NOW confidence 부족
def test_close_now_low_confidence_rejected(rules):
    d = MonitorDecision(action="CLOSE_NOW", confidence=6, reason="불안")
    out, v = validate_monitor(d, 140_000, 150_000, 142_000, 142_000, rules)
    assert out.action == "HOLD"
    assert any("confidence 부족" in s for s in v)
