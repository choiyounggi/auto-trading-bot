"""ENTRY LLM 결정 저장/라벨링 테스트."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from src.reconciler.__main__ import _calc_cumulative_pnl, _entry_label, _has_reportable_activity
from src.storage.models import LLMDecision, create_all
from src.storage.repository import Repo


def _repo(tmp_path) -> Repo:
    repo = Repo(tmp_path / "trades.sqlite")
    create_all(repo.engine)
    return repo


def test_log_entry_decision_is_queryable_when_due(tmp_path):
    repo = _repo(tmp_path)
    today = date(2026, 6, 10)

    decision_id = repo.log_llm_decision(
        position_id=None,
        decision_type="ENTRY",
        model="claude-sonnet",
        source="claude",
        response_text="[]",
        response_json='{"action":"SKIP"}',
        confidence=8,
        action="SKIP",
        elapsed_ms=10,
        ticker="005930",
        name="삼성전자",
        signal_score=5,
        decision_date=today - timedelta(days=7),
        eval_due_date=today,
        price_at_decision=70000,
    )

    due = repo.get_due_entry_decisions(today)

    assert [d.id for d in due] == [decision_id]
    assert due[0].ticker == "005930"
    assert due[0].price_at_decision == 70000


def test_label_llm_decision_excludes_from_due_list(tmp_path):
    repo = _repo(tmp_path)
    today = date(2026, 6, 10)
    decision_id = repo.log_llm_decision(
        position_id=None,
        decision_type="ENTRY",
        model="claude-sonnet",
        source="claude",
        response_text="[]",
        response_json='{"action":"BUY"}',
        confidence=9,
        action="BUY",
        elapsed_ms=10,
        ticker="000660",
        name="SK하이닉스",
        signal_score=6,
        decision_date=today - timedelta(days=7),
        eval_due_date=today,
        price_at_decision=100000,
    )

    repo.label_llm_decision(decision_id, "TRUE_POSITIVE", 0.05)

    assert repo.get_due_entry_decisions(today) == []
    with repo.SessionLocal() as s:
        row = s.execute(select(LLMDecision).where(LLMDecision.id == decision_id)).scalar_one()
        assert row.label == "TRUE_POSITIVE"
        assert row.actual_return == 0.05
        assert row.labeled_at is not None


def test_entry_label_thresholds():
    assert _entry_label("BUY", 0.031) == "TRUE_POSITIVE"
    assert _entry_label("BUY", -0.021) == "FALSE_POSITIVE"
    assert _entry_label("BUY", 0.01) == "NEUTRAL"
    assert _entry_label("SKIP", 0.031) == "FALSE_NEGATIVE"
    assert _entry_label("SKIP", -0.01) == "TRUE_NEGATIVE"


def test_has_reportable_activity_skips_when_everything_is_zero():
    assert not _has_reportable_activity(
        filled=0,
        cancelled=0,
        trades_opened=0,
        trades_closed=0,
        realized_pnl_won=0,
        cumulative_pnl_won=0,
        sl_hits=0,
        labeled_entries=0,
    )


def test_has_reportable_activity_sends_for_trade_pnl_sync_or_label_change():
    base = {
        "filled": 0,
        "cancelled": 0,
        "trades_opened": 0,
        "trades_closed": 0,
        "realized_pnl_won": 0,
        "cumulative_pnl_won": 0,
        "sl_hits": 0,
        "labeled_entries": 0,
    }

    for field in base:
        changed = {**base, field: 1}

        assert _has_reportable_activity(**changed), field


def test_calc_cumulative_pnl_reports_amount_and_percent_from_initial_capital():
    amount, pct = _calc_cumulative_pnl(total_asset_value=30_450_000, capital_baseline=30_000_000)

    assert amount == 450_000
    assert pct == 1.5


def test_calc_cumulative_pnl_handles_loss():
    amount, pct = _calc_cumulative_pnl(total_asset_value=29_700_000, capital_baseline=30_000_000)

    assert amount == -300_000
    assert pct == -1.0
