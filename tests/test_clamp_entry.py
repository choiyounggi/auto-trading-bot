"""진입 결정 가드레일 clamp 테스트."""
from __future__ import annotations

import pytest

from src.guardrails.clamp import clamp_entry
from src.guardrails.rules import TradingRules
from src.llm.schemas import EntryDecision


@pytest.fixture
def rules() -> TradingRules:
    return TradingRules()


def _buy(**overrides) -> EntryDecision:
    base = dict(
        action="BUY",
        entry_strategy="MARKET_OPEN",
        entry_price=100_000,
        size_pct=3.0,    # paper 검증 단계 권장 1~5% 안
        stop_loss_pct=2.0,
        take_profit_pct=3.0,
        max_hold_days=5,
        confidence=9,
        key_thesis="test",
    )
    base.update(overrides)
    return EntryDecision(**base)


# 정상 1
def test_normal_buy_passes_unchanged(rules):
    d = _buy()
    out = clamp_entry(d, rules)
    assert out.action == "BUY"
    assert out.size_pct == 3.0
    assert out.stop_loss_pct == 2.0


# 에러 케이스: confidence 미달
def test_confidence_below_min_becomes_skip(rules):
    d = _buy(confidence=7)
    out = clamp_entry(d, rules)
    assert out.action == "SKIP"


def test_size_pct_clamped_to_min(rules):
    d = _buy(size_pct=0.1)  # < min=0.5 → 0.5로 올림
    out = clamp_entry(d, rules)
    assert out.size_pct == rules.min_size_pct  # 0.5

def test_size_pct_above_max_clamped(rules):
    d = _buy(size_pct=25.0)  # > max=5 → 5로 자름
    out = clamp_entry(d, rules)
    assert out.size_pct == rules.max_size_pct  # 5.0


# 경계값: stop_loss_pct 너무 작음 → min으로 올림
def test_stop_loss_clamped_to_min(rules):
    d = _buy(stop_loss_pct=0.5)
    out = clamp_entry(d, rules)
    assert out.stop_loss_pct == rules.min_stop_loss_pct  # 1.5


# 경계값: stop_loss_pct 너무 큼 → max로 잘림
def test_stop_loss_clamped_to_max(rules):
    d = _buy(stop_loss_pct=8.0)
    out = clamp_entry(d, rules)
    assert out.stop_loss_pct == rules.max_stop_loss_pct  # 3.0


# 경계값: take_profit_pct 폭주 → max로 잘림
def test_take_profit_clamped_to_max(rules):
    d = _buy(take_profit_pct=19.0)
    out = clamp_entry(d, rules)
    assert out.take_profit_pct == rules.max_take_profit_pct  # 10.0


# 경계값: max_hold_days 폭주
def test_max_hold_days_clamped(rules):
    d = _buy(max_hold_days=15)
    out = clamp_entry(d, rules)
    assert out.max_hold_days == rules.max_hold_days  # 5


# SKIP은 변경 없이 통과
def test_skip_passthrough(rules):
    d = EntryDecision(action="SKIP", confidence=0)
    out = clamp_entry(d, rules)
    assert out.action == "SKIP"


# 사이드이펙트: 모든 필드 동시 폭주 → 모두 clamp 동시 적용
def test_multiple_violations_all_clamped(rules):
    d = _buy(size_pct=25.0, stop_loss_pct=8.0, take_profit_pct=19.0, max_hold_days=15)
    out = clamp_entry(d, rules)
    assert out.action == "BUY"
    assert out.size_pct == 5.0  # max clamp
    assert out.stop_loss_pct == 3.0
    assert out.take_profit_pct == 10.0
    assert out.max_hold_days == 5
