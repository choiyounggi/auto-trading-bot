from __future__ import annotations

from src.guardrails.rules import TradingRules
from src.llm.schemas import EntryDecision
from src.orchestrator import entry_decision as mod
from src.orchestrator.entry_decision import AccountSnapshot, evaluate_candidate


def _account() -> AccountSnapshot:
    return AccountSnapshot(
        cash_won=1_000_000,
        open_positions=0,
        daily_pnl_pct=0.0,
        daily_entries_today=0,
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
