from __future__ import annotations

from pathlib import Path

import pytest

from src.guardrails.rules import TradingRules, load_rules
from src.llm.schemas import EntryDecision
from src.orchestrator import entry_decision as mod
from src.orchestrator.entry_decision import (
    AccountSnapshot,
    build_prompt,
    evaluate_candidate,
    select_entries,
)

REPO = Path(__file__).resolve().parents[1]


def _account() -> AccountSnapshot:
    return AccountSnapshot(
        cash_won=1_000_000,
        open_positions=0,
        daily_pnl_pct=0.0,
        daily_entries_today=0,
        total_asset_won=1_000_000,
    )


def _candidate(**overrides) -> dict:
    base = {
        "ticker": "005930",
        "name": "삼성전자",
        "score": 5,
        "triggers": ["TB:20일 고점 종가 돌파"],
        "strategy_id": "turtle_breakout",
        "strategy_score": 5,
        "features": {
            "turtle_stop_loss_pct": 3.0,
            "turtle_take_profit_pct": 9.0,
            "risk_model": "atr20_2x_account_risk",
        },
        "panel_summary": {"last_close": 10_000},
    }
    base.update(overrides)
    return base


def _buy_decision(**overrides) -> EntryDecision:
    base = {
        "action": "BUY",
        "entry_strategy": "MARKET_OPEN",
        "entry_price": 10_000,
        "size_pct": 5.0,
        "stop_loss_pct": 1.5,
        "take_profit_pct": 2.0,
        "max_hold_days": 5,
        "confidence": 9,
        "key_thesis": "breakout",
        "watch_signals": ["20일 고점 유지"],
    }
    base.update(overrides)
    return EntryDecision(**base)


def test_turtle_breakout_uses_atr_stop_and_risk_based_qty(monkeypatch):
    def fake_vote(prompt, n, timeout):
        return _buy_decision(), [{"source": "test", "elapsed_ms": 1, "parse_error": None}]

    monkeypatch.setattr(mod, "vote_entry", fake_vote)
    rules = TradingRules(risk_per_trade_pct=0.05)

    plan, reason = evaluate_candidate(_candidate(), {}, _account(), rules, repo=None)

    assert reason is None
    assert plan is not None
    assert plan.qty == 1  # 1,000,000원 × 0.05% / 300원 리스크 = 1주
    assert plan.stop_loss_price == 9_700
    assert plan.take_profit_price == 10_900
    assert plan.decision.stop_loss_pct == 3.0
    assert plan.decision.take_profit_pct == 9.0
    assert plan.decision.max_hold_days == 15


def test_low_confidence_is_skipped_and_no_plan(monkeypatch):
    def fake_vote(prompt, n, timeout):
        return _buy_decision(confidence=7), [{"source": "test", "elapsed_ms": 1, "parse_error": None}]

    monkeypatch.setattr(mod, "vote_entry", fake_vote)

    plan, reason = evaluate_candidate(_candidate(), {}, _account(), TradingRules(), repo=None)

    assert plan is None
    assert "confidence" in reason


def test_score_below_threshold_skips_before_llm(monkeypatch):
    called = False

    def fake_vote(prompt, n, timeout):
        nonlocal called
        called = True
        return _buy_decision(), []

    monkeypatch.setattr(mod, "vote_entry", fake_vote)

    plan, reason = evaluate_candidate(
        _candidate(score=4),
        {},
        _account(),
        TradingRules(entry_signal_score_min=5),
        repo=None,
    )

    assert plan is None
    assert "signal score" in reason
    assert called is False


def test_overseas_candidate_uses_minor_units_and_usd_budget(monkeypatch):
    def fake_vote(prompt, n, timeout):
        return _buy_decision(entry_price=195, size_pct=5.0), [
            {"source": "test", "elapsed_ms": 1, "parse_error": None}
        ]

    monkeypatch.setattr(mod, "vote_entry", fake_vote)
    rules = TradingRules(
        overseas_paper_capital_usd=10_000,
        overseas_risk_per_trade_pct=0.25,
        overseas_max_size_pct=5.0,
    )
    account = AccountSnapshot(
        cash_won=1_000_000,
        cash_usd=10_000,
        open_positions=0,
        daily_pnl_pct=0.0,
        daily_entries_today=0,
        total_asset_won=1_000_000,
    )
    candidate = _candidate(
        ticker="AAPL",
        name="Apple",
        strategy_id="price_momentum",
        features={
            "asset_class": "overseas_stock",
            "broker_symbol": "AAPL",
            "exchange": "NASD",
            "quote_exchange": "NAS",
            "currency": "USD",
            "price_scale": 100,
            "last_close": 19_500,
        },
        panel_summary={"last_close": 19_500},
    )

    plan, reason = evaluate_candidate(candidate, {}, account, rules, repo=None)

    assert reason is None
    assert plan is not None
    assert plan.asset_class == "overseas_stock"
    assert plan.broker_symbol == "AAPL"
    assert plan.currency == "USD"
    assert plan.price_scale == 100
    assert plan.entry_price_tick == 19_500
    assert plan.qty >= 1



def test_paper_probe_allows_turtle_confidence_75(monkeypatch):
    def fake_vote(prompt, n, timeout):
        return _buy_decision(confidence=7.6, size_pct=5.0), [
            {"source": "test", "elapsed_ms": 1, "parse_error": None}
        ]

    monkeypatch.setattr(mod, "vote_entry", fake_vote)

    plan, reason = evaluate_candidate(_candidate(), {}, _account(), TradingRules(), repo=None)

    assert reason is None
    assert plan is not None
    assert plan.strategy_id == "turtle_breakout"
    assert plan.decision.size_pct <= 1.0


def test_paper_probe_does_not_allow_low_confidence_flow(monkeypatch):
    def fake_vote(prompt, n, timeout):
        return _buy_decision(confidence=7.6), [
            {"source": "test", "elapsed_ms": 1, "parse_error": None}
        ]

    monkeypatch.setattr(mod, "vote_entry", fake_vote)
    candidate = _candidate(strategy_id="flow_momentum", triggers=["A2:연속 순매수"])

    plan, reason = evaluate_candidate(candidate, {}, _account(), TradingRules(), repo=None)

    assert plan is None
    assert "confidence" in reason
    assert "paper_probe" not in reason



def test_overseas_distribution_target_overrides_llm_take_profit(monkeypatch):
    def fake_vote(prompt, n, timeout):
        return _buy_decision(entry_price=210, size_pct=5.0, stop_loss_pct=3.0, take_profit_pct=10.0), [
            {"source": "test", "elapsed_ms": 1, "parse_error": None}
        ]

    monkeypatch.setattr(mod, "vote_entry", fake_vote)
    rules = TradingRules(
        overseas_paper_capital_usd=10_000,
        overseas_risk_per_trade_pct=0.25,
        overseas_max_size_pct=5.0,
    )
    account = AccountSnapshot(
        cash_won=1_000_000,
        cash_usd=10_000,
        open_positions=0,
        daily_pnl_pct=0.0,
        daily_entries_today=0,
        total_asset_won=1_000_000,
    )
    candidate = _candidate(
        ticker="AAPL",
        name="Apple",
        strategy_id="us_breakout_20",
        features={
            "asset_class": "overseas_stock",
            "broker_symbol": "AAPL",
            "exchange": "NASD",
            "quote_exchange": "NAS",
            "currency": "USD",
            "price_scale": 100,
            "last_close": 19_500,
            "turtle_stop_loss_pct": 6.0,
            "turtle_take_profit_pct": 18.0,
            "normal_stop_loss_pct": 1.8,
            "normal_take_profit_pct": 2.5,
            "daily_return_zscore_60d": 0.7,
            "price_zscore_20d": 1.2,
        },
        panel_summary={"last_close": 19_500},
    )

    plan, reason = evaluate_candidate(candidate, {}, account, rules, repo=None)

    assert reason is None
    assert plan is not None
    assert plan.decision.stop_loss_pct == 1.8
    assert plan.decision.take_profit_pct == 2.5
    assert plan.stop_loss_price == 19_149
    assert plan.take_profit_price == 19_988


# ---------------------------------------------------------------------------
# 태스크 03: 사이징 기준 전환(예수금 → 총자산) + 배치 예산 강제
# ---------------------------------------------------------------------------

_SIZING_SWITCH_RULES = TradingRules(
    max_size_pct=15.0,
    min_size_pct=0.5,
    risk_per_trade_pct=0.25,
    min_stop_loss_pct=1.5,
    max_stop_loss_pct=3.0,
    entry_min_confidence=8,
)


def _domestic_candidate(ticker: str, name: str = "종목") -> dict:
    return {
        "ticker": ticker,
        "name": name,
        "score": 5,
        "triggers": ["A2:연속 순매수"],
        "strategy_id": "price_momentum",
        "strategy_score": 5,
        "features": {},
        "panel_summary": {"last_close": 10_000},
    }


def test_total_asset_won_changes_sizing_base_from_cash_won(monkeypatch):
    """정상 — total_asset_won 이 사이징 기준이 되고, 0 이면 cash_won 으로 폴백해
    수량이 실제로 달라진다 (폴백이 다른 경로임을 증명)."""
    def fake_vote(prompt, n, timeout):
        return _buy_decision(entry_price=10_000, size_pct=10.0, stop_loss_pct=1.5), [
            {"source": "test", "elapsed_ms": 1, "parse_error": None}
        ]

    monkeypatch.setattr(mod, "vote_entry", fake_vote)

    account_total_asset = AccountSnapshot(
        cash_won=25_000_000,
        total_asset_won=30_000_000,
        open_positions=0,
        daily_pnl_pct=0.0,
        daily_entries_today=0,
    )
    plan_a, reason_a = evaluate_candidate(
        _domestic_candidate("T1"), {}, account_total_asset, _SIZING_SWITCH_RULES, repo=None,
    )
    assert reason_a is None and plan_a is not None
    assert plan_a.qty == 300  # 30,000,000 × 10% / 10,000

    account_fallback = AccountSnapshot(
        cash_won=25_000_000,
        total_asset_won=0,
        open_positions=0,
        daily_pnl_pct=0.0,
        daily_entries_today=0,
    )
    plan_b, reason_b = evaluate_candidate(
        _domestic_candidate("T1"), {}, account_fallback, _SIZING_SWITCH_RULES, repo=None,
    )
    assert reason_b is None and plan_b is not None
    assert plan_b.qty == 250  # 25,000,000(cash_won 폴백) × 10% / 10,000

    assert plan_a.qty != plan_b.qty


def test_deployable_won_caps_batch_notional_and_last_skip_is_budget_exhausted(monkeypatch):
    """D12 증거 — 한 배치의 채택된 주문 명목 합계가 deployable_won 을 넘지 않고,
    예산 소진 후 후보는 budget_exhausted 로 skip 된다."""
    def fake_vote(prompt, n, timeout):
        return _buy_decision(entry_price=500_000, size_pct=30.0, stop_loss_pct=1.5), [
            {"source": "test", "elapsed_ms": 1, "parse_error": None}
        ]

    monkeypatch.setattr(mod, "vote_entry", fake_vote)

    rules = TradingRules(
        max_size_pct=30.0,
        min_size_pct=0.5,
        risk_per_trade_pct=0.25,
        min_stop_loss_pct=1.5,
        max_stop_loss_pct=3.0,
        entry_min_confidence=8,
        max_daily_entries=10,
        strategy_max_daily_entries={"price_momentum": 10},
    )
    account = AccountSnapshot(
        cash_won=1_000_000,
        total_asset_won=100_000_000,
        open_positions=0,
        daily_pnl_pct=0.0,
        daily_entries_today=0,
    )
    candidates = [_domestic_candidate("T1"), _domestic_candidate("T2"), _domestic_candidate("T3")]

    plans, skips = select_entries(
        candidates, {}, account, rules,
        kill_switch_file="tests/__nonexistent_kill_switch__",
        deployable_won=1_000_000,
    )

    total_notional = sum(p.entry_price_tick * p.qty for p in plans)
    assert total_notional <= 1_000_000
    assert total_notional > 0
    assert skips[-1].reason == "budget_exhausted"


def test_budget_won_zero_yields_qty_zero_with_reason(monkeypatch):
    def fake_vote(prompt, n, timeout):
        return _buy_decision(entry_price=10_000, size_pct=10.0, stop_loss_pct=1.5), [
            {"source": "test", "elapsed_ms": 1, "parse_error": None}
        ]

    monkeypatch.setattr(mod, "vote_entry", fake_vote)

    account = AccountSnapshot(
        cash_won=25_000_000, total_asset_won=30_000_000,
        open_positions=0, daily_pnl_pct=0.0, daily_entries_today=0,
    )
    plan, reason = evaluate_candidate(
        _domestic_candidate("T1"), {}, account, _SIZING_SWITCH_RULES, repo=None, budget_won=0,
    )
    assert plan is None
    assert "budget_won" in reason


def test_budget_won_smaller_than_price_yields_qty_zero(monkeypatch):
    def fake_vote(prompt, n, timeout):
        return _buy_decision(entry_price=10_000, size_pct=10.0, stop_loss_pct=1.5), [
            {"source": "test", "elapsed_ms": 1, "parse_error": None}
        ]

    monkeypatch.setattr(mod, "vote_entry", fake_vote)

    account = AccountSnapshot(
        cash_won=25_000_000, total_asset_won=30_000_000,
        open_positions=0, daily_pnl_pct=0.0, daily_entries_today=0,
    )
    plan, reason = evaluate_candidate(
        _domestic_candidate("T1"), {}, account, _SIZING_SWITCH_RULES, repo=None, budget_won=999,
    )
    assert plan is None
    assert "budget_won" in reason


def test_budget_won_none_matches_unlimited_behavior(monkeypatch):
    def fake_vote(prompt, n, timeout):
        return _buy_decision(entry_price=10_000, size_pct=10.0, stop_loss_pct=1.5), [
            {"source": "test", "elapsed_ms": 1, "parse_error": None}
        ]

    monkeypatch.setattr(mod, "vote_entry", fake_vote)

    account = AccountSnapshot(
        cash_won=25_000_000, total_asset_won=30_000_000,
        open_positions=0, daily_pnl_pct=0.0, daily_entries_today=0,
    )
    plan_default, reason_default = evaluate_candidate(
        _domestic_candidate("T1"), {}, account, _SIZING_SWITCH_RULES, repo=None,
    )
    plan_explicit_none, reason_explicit_none = evaluate_candidate(
        _domestic_candidate("T1"), {}, account, _SIZING_SWITCH_RULES, repo=None, budget_won=None,
    )
    assert reason_default is None and reason_explicit_none is None
    assert plan_default.qty == plan_explicit_none.qty == 300


def test_overseas_candidate_not_starved_by_domestic_budget_exhaustion(monkeypatch):
    """해외 불변 — 원화 배치가능액이 0(소진)이어도 해외 후보는 굶기지 않는다."""
    def fake_vote(prompt, n, timeout):
        return _buy_decision(entry_price=195, size_pct=5.0, stop_loss_pct=1.5), [
            {"source": "test", "elapsed_ms": 1, "parse_error": None}
        ]

    monkeypatch.setattr(mod, "vote_entry", fake_vote)

    rules = TradingRules(
        overseas_paper_capital_usd=10_000,
        overseas_risk_per_trade_pct=0.25,
        overseas_max_size_pct=5.0,
        entry_min_confidence=8,
    )
    account = AccountSnapshot(
        cash_won=1_000_000,
        cash_usd=10_000,
        total_asset_won=30_000_000,
        open_positions=0,
        daily_pnl_pct=0.0,
        daily_entries_today=0,
    )
    candidate = {
        "ticker": "AAPL",
        "name": "Apple",
        "score": 5,
        "triggers": ["TB:20일 고점 종가 돌파"],
        "strategy_id": "price_momentum",
        "strategy_score": 5,
        "features": {
            "asset_class": "overseas_stock",
            "broker_symbol": "AAPL",
            "exchange": "NASD",
            "quote_exchange": "NAS",
            "currency": "USD",
            "price_scale": 100,
            "last_close": 19_500,
        },
        "panel_summary": {"last_close": 19_500},
    }

    plans, skips = select_entries(
        [candidate], {}, account, rules,
        kill_switch_file="tests/__nonexistent_kill_switch__",
        deployable_won=0,
    )

    assert len(plans) == 1
    assert plans[0].asset_class == "overseas_stock"
    assert skips == []


def test_keyword_only_enforced_for_new_budget_and_quota_params(monkeypatch):
    """D4 강제 — bare `*` 를 빠뜨리면 이 셋이 조용히 통과해 버린다."""
    def fake_vote(prompt, n, timeout):
        return _buy_decision(), [{"source": "test", "elapsed_ms": 1, "parse_error": None}]

    monkeypatch.setattr(mod, "vote_entry", fake_vote)
    account = _account()

    with pytest.raises(TypeError):
        evaluate_candidate(_candidate(), {}, account, TradingRules(), None, 5_000_000)  # type: ignore[misc]

    with pytest.raises(TypeError):
        select_entries([], {}, account, TradingRules(), None, None, 5_000_000)  # type: ignore[misc]

    with pytest.raises(TypeError):
        select_entries([], {}, account, TradingRules(), None, None, None, 3)  # type: ignore[misc]


def test_quota_override_caps_accepted_count(monkeypatch):
    """정상 — quota_override 가 실제로 채택 건수를 제한한다 (태스크 05가 재배치
    예산으로 넘길 통로이므로 실제 동작해야 한다)."""
    def fake_vote(prompt, n, timeout):
        return _buy_decision(entry_price=10_000, size_pct=10.0, stop_loss_pct=1.5), [
            {"source": "test", "elapsed_ms": 1, "parse_error": None}
        ]

    monkeypatch.setattr(mod, "vote_entry", fake_vote)

    rules = TradingRules(
        max_size_pct=15.0,
        min_size_pct=0.5,
        risk_per_trade_pct=0.25,
        min_stop_loss_pct=1.5,
        max_stop_loss_pct=3.0,
        entry_min_confidence=8,
        max_daily_entries=12,
        strategy_max_daily_entries={"price_momentum": 10},
    )
    account = AccountSnapshot(
        cash_won=25_000_000, total_asset_won=30_000_000,
        open_positions=0, daily_pnl_pct=0.0, daily_entries_today=0,
    )
    candidates = [_domestic_candidate(f"T{i}") for i in range(5)]

    plans_capped, skips_capped = select_entries(
        candidates, {}, account, rules,
        kill_switch_file="tests/__nonexistent_kill_switch__",
        quota_override=2,
    )
    assert len(plans_capped) == 2
    assert any(s.reason == "quota_exhausted" for s in skips_capped)

    plans_default, _ = select_entries(
        candidates, {}, account, rules,
        kill_switch_file="tests/__nonexistent_kill_switch__",
        quota_override=None,
    )
    assert len(plans_default) == 5  # rules.max_daily_entries=12 가 쓰인다


# ---------------------------------------------------------------------------
# 태스크 04: LLM 프롬프트 — 자본 기준을 총자산으로, 사이징 문구를 D15 값으로
# ---------------------------------------------------------------------------

def test_build_prompt_shows_total_asset_invested_and_utilization():
    account = AccountSnapshot(
        cash_won=25_000_000, total_asset_won=30_000_000, invested_won=5_000_000,
        open_positions=2, daily_pnl_pct=0.0, daily_entries_today=0,
    )
    rules = TradingRules(target_utilization_pct=90.0)

    prompt = build_prompt(_domestic_candidate("T1"), {}, account, rules)

    assert "30,000,000" in prompt
    assert "5,000,000" in prompt
    assert "16.7%" in prompt
    assert "목표 90%" in prompt


def test_build_prompt_shows_deployable_won():
    account = AccountSnapshot(
        cash_won=25_000_000, total_asset_won=30_000_000, invested_won=5_000_000,
        open_positions=0, daily_pnl_pct=0.0, daily_entries_today=0,
    )
    prompt = build_prompt(
        _domestic_candidate("T1"), {}, account, TradingRules(), deployable_won=22_000_000,
    )
    assert "이번 배치 가능액: 22,000,000 원" in prompt


def test_build_prompt_size_pct_wording_matches_loaded_config_d15_guard():
    """D15 동기화 가드 — 프롬프트의 하드코딩 문구가 실제 config 값과 갈라지면 빨개진다."""
    rules = load_rules(REPO / "config" / "trading_rules.yaml")
    account = AccountSnapshot(
        cash_won=25_000_000, total_asset_won=30_000_000, invested_won=5_000_000,
        open_positions=0, daily_pnl_pct=0.0, daily_entries_today=0,
    )
    prompt = build_prompt(_domestic_candidate("T1"), {}, account, rules)
    assert f"clamp 범위 {rules.min_size_pct:.0f}~{rules.max_size_pct:.0f}%" in prompt


def test_build_prompt_zero_total_asset_no_division_error():
    account = AccountSnapshot(
        cash_won=0, total_asset_won=0, invested_won=0,
        open_positions=0, daily_pnl_pct=0.0, daily_entries_today=0,
    )
    prompt = build_prompt(_domestic_candidate("T1"), {}, account, TradingRules())
    assert "0.0%" in prompt


def test_build_prompt_default_deployable_won_is_zero():
    account = AccountSnapshot(
        cash_won=25_000_000, total_asset_won=30_000_000, invested_won=5_000_000,
        open_positions=0, daily_pnl_pct=0.0, daily_entries_today=0,
    )
    prompt = build_prompt(_domestic_candidate("T1"), {}, account, TradingRules())
    assert "이번 배치 가능액: 0 원" in prompt


def test_build_prompt_rejects_positional_deployable_won():
    account = AccountSnapshot(
        cash_won=25_000_000, total_asset_won=30_000_000,
        open_positions=0, daily_pnl_pct=0.0, daily_entries_today=0,
    )
    with pytest.raises(TypeError):
        build_prompt(_domestic_candidate("T1"), {}, account, TradingRules(), 100)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# D9a: 조기 daily_entry_limit 게이트가 quota_override 를 무시하는 결함 회귀 테스트
# (2026-08-13 t2 워커가 발견, 코디네이터 지시로 t2가 수정)
# ---------------------------------------------------------------------------

def test_quota_override_lifts_early_daily_limit_gate(monkeypatch):
    """D9a — 아침 예산이 rules.max_daily_entries 를 이미 다 채웠어도(daily_entries_today==
    max_daily_entries), quota_override 로 유효 상한을 올렸으면 §2의 조기 게이트가
    막지 않고 최소 1건은 채택되어야 한다."""
    def fake_vote(prompt, n, timeout):
        return _buy_decision(entry_price=10_000, size_pct=5.0, stop_loss_pct=1.5), [
            {"source": "test", "elapsed_ms": 1, "parse_error": None}
        ]

    monkeypatch.setattr(mod, "vote_entry", fake_vote)

    rules = TradingRules(
        max_size_pct=15.0,
        min_size_pct=0.5,
        risk_per_trade_pct=0.25,
        min_stop_loss_pct=1.5,
        max_stop_loss_pct=3.0,
        entry_min_confidence=8,
        max_daily_entries=12,
        cash_deploy_max_daily_entries=6,
        strategy_max_daily_entries={"price_momentum": 10},
    )
    account = AccountSnapshot(
        cash_won=25_000_000, total_asset_won=30_000_000,
        open_positions=0, daily_pnl_pct=0.0, daily_entries_today=12,
    )
    candidates = [_domestic_candidate("T1")]

    plans, skips = select_entries(
        candidates, {}, account, rules,
        kill_switch_file="tests/__nonexistent_kill_switch__",
        quota_override=rules.max_daily_entries + rules.cash_deploy_max_daily_entries,
    )

    assert len(plans) >= 1
    assert not any(s.reason == "daily_entry_limit" for s in skips)


def test_daily_limit_gate_still_blocks_without_quota_override(monkeypatch):
    """D9a 회귀 — quota_override 를 안 넘기면 daily_entries_today >= max_daily_entries
    에서 여전히 즉시 daily_entry_limit 으로 전종목 차단되는 기존 동작이 유지된다."""
    def fake_vote(prompt, n, timeout):
        return _buy_decision(entry_price=10_000, size_pct=5.0, stop_loss_pct=1.5), [
            {"source": "test", "elapsed_ms": 1, "parse_error": None}
        ]

    monkeypatch.setattr(mod, "vote_entry", fake_vote)

    rules = TradingRules(max_daily_entries=12, cash_deploy_max_daily_entries=6)
    account = AccountSnapshot(
        cash_won=25_000_000, total_asset_won=30_000_000,
        open_positions=0, daily_pnl_pct=0.0, daily_entries_today=12,
    )
    candidates = [_domestic_candidate("T1")]

    plans, skips = select_entries(
        candidates, {}, account, rules,
        kill_switch_file="tests/__nonexistent_kill_switch__",
        quota_override=None,
    )

    assert plans == []
    assert all(s.reason == "daily_entry_limit" for s in skips)
