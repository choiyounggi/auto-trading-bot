"""가동률 config 키 + 진입 파라미터 상향 — 로더 계약 테스트 (태스크 01).

D6: 각 신규 키마다 "값을 바꾸면 로드 결과가 바뀐다"를 검증한다. 파싱만
확인하는 테스트는 인정하지 않는다. 각 키가 실제 동작을 바꾼다는 증명은
소비처(태스크 02/03·04/05)가 한다 — 이 파일은 로더까지만 검증한다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.guardrails.rules import TradingRules, load_rules

REPO = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO / "config" / "trading_rules.yaml"


def test_real_config_loads_upgraded_values() -> None:
    rules = load_rules(CONFIG_PATH)
    assert rules.target_utilization_pct == 90.0
    assert rules.cash_buffer_pct == 10.0
    assert rules.cash_deploy_max_candidates_per_run == 4
    assert rules.max_size_pct == 15.0
    assert rules.min_size_pct == 5.0
    assert rules.max_position_count == 18
    assert rules.max_daily_entries == 12


def test_strategy_budgets_upgraded_to_three() -> None:
    # value_quality 는 mode: filter_only 로 max_daily_entries 를 아예 갖지 않는다
    # (태스크 01이 손대지 않은 대상) — 매매 예산이 있는 전략만 검증한다.
    rules = load_rules(CONFIG_PATH)
    trading_strategies = {
        name: n
        for name, n in rules.strategy_max_daily_entries.items()
        if name != "value_quality"
    }
    assert trading_strategies, "no active trading strategies loaded"
    for name, n in trading_strategies.items():
        assert n == 3, f"{name} max_daily_entries expected 3, got {n}"


CAPITAL_KEY_CASES = [
    ("capital", "target_utilization_pct", 55.0, "target_utilization_pct"),
    ("capital", "cash_buffer_pct", 25.0, "cash_buffer_pct"),
    ("cash_deploy", "enabled", False, "cash_deploy_enabled"),
    ("cash_deploy", "max_daily_entries", 2, "cash_deploy_max_daily_entries"),
    ("cash_deploy", "max_candidates_per_run", 9, "cash_deploy_max_candidates_per_run"),
    ("cash_deploy", "min_deploy_won", 123_456, "cash_deploy_min_deploy_won"),
    ("cash_deploy", "underrun_warn_pct", 33.0, "cash_deploy_underrun_warn_pct"),
]


@pytest.mark.parametrize("block, yaml_key, value, field_name", CAPITAL_KEY_CASES)
def test_new_key_value_changes_load_result(tmp_path, block, yaml_key, value, field_name) -> None:
    """D6 강제 — 신규 7개 키 전부. 기본값이 아니라 파일 값이 이긴다."""
    yaml_text = f"{block}:\n  {yaml_key}: {value!r}\n"
    p = tmp_path / "trading_rules.yaml"
    p.write_text(yaml_text, encoding="utf-8")

    rules = load_rules(p)
    default_value = getattr(TradingRules(), field_name)

    assert getattr(rules, field_name) == value
    assert value != default_value, "test value must differ from dataclass default"


def test_missing_path_falls_back_to_defaults() -> None:
    rules = load_rules(Path("/nonexistent/path/does-not-exist.yaml"))
    defaults = TradingRules()
    assert rules.target_utilization_pct == defaults.target_utilization_pct
    assert rules.cash_deploy_max_daily_entries == defaults.cash_deploy_max_daily_entries


def test_config_without_capital_or_cash_deploy_blocks_uses_defaults(tmp_path) -> None:
    p = tmp_path / "trading_rules.yaml"
    p.write_text("guardrails:\n  max_size_pct: 15.0\n", encoding="utf-8")

    rules = load_rules(p)
    defaults = TradingRules()
    assert rules.target_utilization_pct == defaults.target_utilization_pct
    assert rules.cash_buffer_pct == defaults.cash_buffer_pct
    assert rules.cash_deploy_enabled == defaults.cash_deploy_enabled
    assert rules.cash_deploy_max_daily_entries == defaults.cash_deploy_max_daily_entries
    assert rules.cash_deploy_max_candidates_per_run == defaults.cash_deploy_max_candidates_per_run
    assert rules.cash_deploy_min_deploy_won == defaults.cash_deploy_min_deploy_won
    assert rules.cash_deploy_underrun_warn_pct == defaults.cash_deploy_underrun_warn_pct


def test_empty_cash_deploy_block_uses_defaults_not_none(tmp_path) -> None:
    p = tmp_path / "trading_rules.yaml"
    p.write_text("cash_deploy: {}\n", encoding="utf-8")

    rules = load_rules(p)
    defaults = TradingRules()
    assert rules.cash_deploy_enabled == defaults.cash_deploy_enabled
    assert rules.cash_deploy_max_daily_entries == defaults.cash_deploy_max_daily_entries
    assert rules.cash_deploy_max_candidates_per_run == defaults.cash_deploy_max_candidates_per_run
    assert rules.cash_deploy_min_deploy_won == defaults.cash_deploy_min_deploy_won
    assert rules.cash_deploy_underrun_warn_pct == defaults.cash_deploy_underrun_warn_pct
    for value in (
        rules.cash_deploy_enabled,
        rules.cash_deploy_max_daily_entries,
        rules.cash_deploy_max_candidates_per_run,
        rules.cash_deploy_min_deploy_won,
        rules.cash_deploy_underrun_warn_pct,
    ):
        assert value is not None


def test_size_pct_dominates_risk_cap_at_every_stop_loss_width() -> None:
    """정합성 회귀 가드 — plan.md 상향값 근거의 부등식. 깨지면 size_pct 상향이 무의미."""
    rules = load_rules(CONFIG_PATH)
    risk_implied_cap_pct = rules.risk_per_trade_pct / rules.max_stop_loss_pct * 100
    assert risk_implied_cap_pct > rules.max_size_pct
