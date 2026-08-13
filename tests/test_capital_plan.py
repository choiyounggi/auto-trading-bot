"""compute_capital_plan / position_eval_won — 순수 계산 테스트 (태스크 02).

기대값은 손으로 계산한 숫자를 literal 로 적는다. 프로덕션 수식을 테스트에
재구현하면 어떤 버그도 못 잡는다.
"""
from __future__ import annotations

import pytest

from src.guardrails.rules import TradingRules
from src.orchestrator.capital import CapitalPlan, compute_capital_plan, position_eval_won


@pytest.fixture
def rules() -> TradingRules:
    return TradingRules(target_utilization_pct=90.0, cash_buffer_pct=10.0)


def test_normal_case_computes_target_buffer_and_deployable(rules) -> None:
    plan = compute_capital_plan(
        total_asset_won=30_000_000,
        position_eval_won=5_000_000,
        pending_notional_won=0,
        buying_power_won=25_000_000,
        rules=rules,
    )
    assert plan.utilization_pct == pytest.approx(16.666666, abs=0.001)
    assert plan.buffer_won == 3_000_000
    assert plan.deployable_won == 22_000_000
    assert plan.invested_won == 5_000_000
    assert plan.total_asset_won == 30_000_000
    assert plan.buying_power_won == 25_000_000
    assert plan.target_pct == 90.0


def test_buying_power_caps_deployable_below_target_gap(rules) -> None:
    """이 모듈의 핵심 — 목표만 보고 계산하면 실제로 못 사는 금액을 배치하게 된다."""
    plan = compute_capital_plan(
        total_asset_won=30_000_000,
        position_eval_won=0,
        pending_notional_won=0,
        buying_power_won=5_000_000,
        rules=rules,
    )
    # gap = 27,000,000 이지만 spendable = 5,000,000 - 3,000,000 = 2,000,000
    assert plan.deployable_won == 2_000_000


def test_pending_notional_reduces_deployable_by_exact_amount(rules) -> None:
    """D7 증거 — PENDING 명목이 invested 에 합산되어 deployable 을 정확히 그만큼 줄인다."""
    without_pending = compute_capital_plan(
        total_asset_won=30_000_000,
        position_eval_won=5_000_000,
        pending_notional_won=0,
        buying_power_won=25_000_000,
        rules=rules,
    )
    with_pending = compute_capital_plan(
        total_asset_won=30_000_000,
        position_eval_won=5_000_000,
        pending_notional_won=10_000_000,
        buying_power_won=25_000_000,
        rules=rules,
    )
    assert with_pending.invested_won == without_pending.invested_won + 10_000_000
    assert without_pending.deployable_won - with_pending.deployable_won == 10_000_000
    assert with_pending.deployable_won == 12_000_000


def test_already_over_target_deployable_is_zero_not_negative(rules) -> None:
    plan = compute_capital_plan(
        total_asset_won=30_000_000,
        position_eval_won=28_000_000,
        pending_notional_won=0,
        buying_power_won=5_000_000,
        rules=rules,
    )
    assert plan.deployable_won == 0


def test_buying_power_below_buffer_deployable_is_zero(rules) -> None:
    plan = compute_capital_plan(
        total_asset_won=30_000_000,
        position_eval_won=0,
        pending_notional_won=0,
        buying_power_won=1_000_000,
        rules=rules,
    )
    assert plan.deployable_won == 0


def test_zero_total_asset_no_division_error(rules) -> None:
    plan = compute_capital_plan(
        total_asset_won=0,
        position_eval_won=0,
        pending_notional_won=0,
        buying_power_won=5_000_000,
        rules=rules,
    )
    assert plan.utilization_pct == 0.0
    assert plan.deployable_won == 0


def test_position_eval_won_helper() -> None:
    assert position_eval_won([]) == 0
    assert position_eval_won([{}]) == 0
    assert position_eval_won([{"eval_amt": 100}, {"eval_amt": 200}]) == 300


def test_compute_capital_plan_rejects_positional_args(rules) -> None:
    with pytest.raises(TypeError):
        compute_capital_plan(0, 0, 0, 0, rules)  # type: ignore[misc]
