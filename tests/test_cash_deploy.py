"""장중 현금 재배치 (`run_cash_deploy`) 테스트.

client / repo 는 전부 가짜 객체로 주입한다 — KIS도 실제 DB도 건드리지 않는다.
select_entries 는 실제 함수를 쓰되 vote_entry 를 monkeypatch 한다
(tests/test_entry_risk_sizing.py 의 monkeypatch.setattr(mod, "vote_entry", fake_vote) 선례).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.guardrails.rules import TradingRules
from src.llm.schemas import EntryDecision
from src.orchestrator import entry_decision as entry_mod
from src.orchestrator.cash_deploy import (
    fetch_live_quotes,
    refresh_panel,
    compute_pending_notional,
    pick_candidates,
    run_cash_deploy,
    should_warn_underrun,
)


# ---------------------------------------------------------------------------
# 가짜 객체
# ---------------------------------------------------------------------------

class FakeRepo:
    def __init__(self, pending=None, active=None, today_entries=0, duplicates=None,
                 closed_today=None):
        self._pending = pending or []
        self._active = active or []
        self._today_entries = today_entries
        self._duplicates = duplicates or set()
        self._closed_today = closed_today or set()
        self.inserted: list[dict] = []

    def get_tickers_closed_today(self):
        return self._closed_today

    def get_pending_positions(self):
        return self._pending

    def get_active_positions(self):
        return self._active

    def get_today_entries(self):
        return self._today_entries

    def is_duplicate(self, ticker: str) -> bool:
        return ticker in self._duplicates

    def insert_position(self, **kwargs) -> int:
        self.inserted.append(kwargs)
        return len(self.inserted)

    def log_llm_decision(self, **kwargs) -> None:
        """evaluate_candidate 가 항상 호출하는 감사로그 — 내용은 검증 대상이 아니다."""


def _pending_position(entry_price_target=10_000, entry_qty=1_000):
    return SimpleNamespace(entry_price_target=entry_price_target, entry_qty=entry_qty)


class FakeOrderResult:
    def __init__(self, accepted=True, broker_order_id="ORD1", msg1=""):
        self.accepted = accepted
        self.broker_order_id = broker_order_id
        self.raw = {"msg1": msg1}


class FakeClient:
    def __init__(self, balance, deposit=None, deposit_error=None, submit_accept=True,
                 submit_msg1="", quotes=None, quote_error=None, d_change=None):
        self._quotes = quotes if quotes is not None else {}
        self._d_change = d_change or {}
        self._quote_error = quote_error
        self.quote_calls: list[str] = []
        self._balance = balance
        self._deposit = deposit
        self._deposit_error = deposit_error
        self._submit_accept = submit_accept
        self._submit_msg1 = submit_msg1
        self.get_balance_calls = 0
        self.get_deposit_calls = 0
        self.submit_calls: list[tuple] = []

    def get_balance(self):
        self.get_balance_calls += 1
        return self._balance

    def get_deposit(self):
        self.get_deposit_calls += 1
        if self._deposit_error is not None:
            raise self._deposit_error
        return self._deposit

    def get_quote(self, ticker):
        self.quote_calls.append(ticker)
        if self._quote_error is not None:
            raise self._quote_error
        px = self._quotes.get(ticker, 10_000)
        if px is None:
            return None
        return SimpleNamespace(ticker=ticker, current_price=px,
                               d_change_pct=self._d_change.get(ticker, 0.0))

    def submit_buy(self, ticker, qty, price, order_type="limit"):
        self.submit_calls.append((ticker, qty, price, order_type))
        return FakeOrderResult(accepted=self._submit_accept, broker_order_id=f"ORD-{ticker}", msg1=self._submit_msg1)


def _balance(cash=25_000_000, total_eval=30_000_000, positions=None):
    return SimpleNamespace(cash=cash, total_eval=total_eval, positions=positions or [])


def _rules(**overrides) -> TradingRules:
    base = dict(
        max_daily_entries=12,
        cash_deploy_enabled=True,
        cash_deploy_max_daily_entries=6,
        cash_deploy_max_candidates_per_run=4,
        cash_deploy_min_deploy_won=500_000,
        cash_deploy_underrun_warn_pct=70.0,
        target_utilization_pct=90.0,
        cash_buffer_pct=10.0,
        max_position_count=18,
        max_size_pct=15.0,
        min_size_pct=0.5,
        risk_per_trade_pct=0.25,
        min_stop_loss_pct=1.5,
        max_stop_loss_pct=3.0,
        entry_min_confidence=8,
        strategy_max_daily_entries={"flow_momentum": 10},
    )
    base.update(overrides)
    return TradingRules(**base)


def _candidate(ticker="005930", name="삼성전자", score=8, asset_class="domestic_stock", **overrides) -> dict:
    base = {
        "ticker": ticker,
        "name": name,
        "score": score,
        "triggers": ["A2:연속 순매수"],
        "strategy_id": "flow_momentum",
        "strategy_score": score,
        "features": {},
        "panel_summary": {"last_close": 10_000},
        "asset_class": asset_class,
    }
    base.update(overrides)
    return base


def _fake_vote(entry_price=10_000, size_pct=1.0, stop_loss_pct=2.0, take_profit_pct=4.0, confidence=9):
    def fake(prompt, n, timeout):
        decision = EntryDecision(
            action="BUY",
            entry_strategy="MARKET_OPEN",
            entry_price=entry_price,
            size_pct=size_pct,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            max_hold_days=5,
            confidence=confidence,
            key_thesis="test",
            watch_signals=[],
        )
        return decision, [{"source": "test", "elapsed_ms": 1, "parse_error": None}]

    return fake


def _noop_send():
    sent: list[str] = []
    return sent, (lambda msg: sent.append(msg))


@pytest.fixture(autouse=True)
def _isolate_warn_marker(monkeypatch, tmp_path):
    """실제 data/logs/.cash_deploy_underrun 를 절대 건드리지 않는다 — 테스트끼리도
    격리해 하루 1회 마커가 실행 순서에 따라 새다 낡다 하지 않게 한다."""
    monkeypatch.setattr("src.orchestrator.cash_deploy._WARN_MARKER", tmp_path / "underrun_marker")


# ---------------------------------------------------------------------------
# compute_pending_notional
# ---------------------------------------------------------------------------

def test_compute_pending_notional_empty_is_zero():
    assert compute_pending_notional([]) == 0


def test_compute_pending_notional_none_price_or_qty_counts_as_zero():
    positions = [
        _pending_position(entry_price_target=None, entry_qty=1_000),
        _pending_position(entry_price_target=10_000, entry_qty=None),
    ]
    assert compute_pending_notional(positions) == 0


def test_compute_pending_notional_sums_multiple_positions():
    positions = [
        _pending_position(entry_price_target=10_000, entry_qty=1_000),  # 10,000,000
        _pending_position(entry_price_target=5_000, entry_qty=200),     # 1,000,000
    ]
    assert compute_pending_notional(positions) == 11_000_000


def test_pending_notional_reduces_deployable_won_by_exact_amount():
    """D7 증거 (핵심) — PENDING 10,000,000원이 있으면 deployable_won 이 정확히
    그만큼 줄어든다. 이게 빠지면 30분 뒤 다음 틱이 같은 현금을 다시 쓴다."""
    from src.orchestrator.capital import compute_capital_plan

    rules = _rules()
    pending_notional = compute_pending_notional([_pending_position(10_000, 1_000)])
    assert pending_notional == 10_000_000

    # buying_power_won 을 넉넉히 둬서 spendable_won 상한에 걸리지 않게 한다 —
    # gap_won(target - invested) 이 유일한 제약이어야 pending 만큼 정확히 줄어든다.
    plan_without = compute_capital_plan(
        total_asset_won=30_000_000, position_eval_won=0,
        pending_notional_won=0, buying_power_won=30_000_000, rules=rules,
    )
    plan_with = compute_capital_plan(
        total_asset_won=30_000_000, position_eval_won=0,
        pending_notional_won=pending_notional, buying_power_won=30_000_000, rules=rules,
    )
    assert plan_without.deployable_won - plan_with.deployable_won == 10_000_000


# ---------------------------------------------------------------------------
# pick_candidates
# ---------------------------------------------------------------------------

def test_pick_candidates_excludes_overseas():
    repo = FakeRepo()
    candidates = [_candidate("T1", asset_class="overseas_stock"), _candidate("T2", asset_class="overseas_stock")]
    picked = pick_candidates(candidates, repo, limit=4)
    assert picked == []


def test_pick_candidates_excludes_overseas_when_asset_class_only_in_features():
    """r1 리뷰 — stock-signal-bot이 실제로 만드는 해외 후보 형태(최상위 asset_class
    키 없음, features 안에만 있음)도 걸러야 한다. 정식 판정은 entry_decision._asset_class()
    이고 그것은 features도 본다."""
    repo = FakeRepo()
    candidates = [{
        "ticker": "AAPL", "name": "Apple", "score": 9,
        "features": {"asset_class": "overseas_stock", "broker_symbol": "AAPL", "currency": "USD"},
    }]
    picked = pick_candidates(candidates, repo, limit=4)
    assert picked == []


def test_pick_candidates_keeps_domestic_when_features_present_without_asset_class_key():
    """features 는 있지만 asset_class 키 자체가 없는 국내 후보는 과잉 차단하지 않고
    기본값(domestic_stock) 경로로 통과한다."""
    repo = FakeRepo()
    candidates = [{
        "ticker": "005930", "name": "삼성전자", "score": 9,
        "features": {"turtle_stop_loss_pct": 3.0},
    }]
    picked = pick_candidates(candidates, repo, limit=4)
    assert [c["ticker"] for c in picked] == ["005930"]


def test_pick_candidates_excludes_duplicates():
    repo = FakeRepo(duplicates={"T1"})
    candidates = [_candidate("T1"), _candidate("T2")]
    picked = pick_candidates(candidates, repo, limit=4)
    assert [c["ticker"] for c in picked] == ["T2"]


def test_pick_candidates_caps_at_limit_sorted_by_score_desc():
    repo = FakeRepo()
    candidates = [_candidate(f"T{i}", score=i) for i in range(10)]
    picked = pick_candidates(candidates, repo, limit=4)
    assert len(picked) == 4
    assert [c["ticker"] for c in picked] == ["T9", "T8", "T7", "T6"]


# ---------------------------------------------------------------------------
# should_warn_underrun
# ---------------------------------------------------------------------------

def test_should_warn_underrun_true_when_no_marker(tmp_path):
    marker = tmp_path / "marker"
    assert should_warn_underrun("2026-08-13", marker=marker) is True
    assert marker.read_text() == "2026-08-13"


def test_should_warn_underrun_false_when_already_warned_today(tmp_path):
    marker = tmp_path / "marker"
    marker.write_text("2026-08-13")
    assert should_warn_underrun("2026-08-13", marker=marker) is False


def test_should_warn_underrun_true_on_new_day(tmp_path):
    marker = tmp_path / "marker"
    marker.write_text("2026-08-12")
    assert should_warn_underrun("2026-08-13", marker=marker) is True


# ---------------------------------------------------------------------------
# run_cash_deploy
# ---------------------------------------------------------------------------

def test_run_cash_deploy_happy_path_accepts_orders_within_budget(monkeypatch, tmp_path):
    """정상: 총자산 30,000,000 / 보유 0 / 매수여력 25,000,000 / 후보 2건."""
    monkeypatch.setattr(entry_mod, "vote_entry", _fake_vote(entry_price=10_000, size_pct=1.0))
    monkeypatch.setattr("src.orchestrator.cash_deploy._KIS_GAP_SEC", 0)

    repo = FakeRepo()
    client = FakeClient(balance=_balance(cash=25_000_000, total_eval=30_000_000), deposit=25_000_000)
    candidates = [_candidate("T1", score=9), _candidate("T2", score=8)]
    sent_info, send_info = _noop_send()
    sent_warn, send_warning = _noop_send()

    n = run_cash_deploy(client, repo, _rules(), candidates, {}, send_info, send_warning)

    assert n == 2
    assert len(repo.inserted) == 2
    notional_sum = sum(qty * price for _, qty, price, _ in client.submit_calls)
    assert notional_sum <= 22_000_000  # deployable_won 상한 이하
    assert not sent_warn


def test_run_cash_deploy_wires_pending_notional_into_capital_plan(monkeypatch):
    """D7 통합 증거 (배선) — run_cash_deploy 가 repo.get_pending_positions() 를
    compute_pending_notional 을 거쳐 compute_capital_plan(pending_notional_won=...)
    에 실제로 전달하는지 확인한다. 순수 함수 합성 테스트만으로는 이 배선 자체가
    검증되지 않는다 (compute_capital_plan 안에서 pending_notional_won 을 무시하도록
    바꿔도 그 테스트는 계속 통과한다)."""
    import src.orchestrator.cash_deploy as cash_deploy_mod

    monkeypatch.setattr(entry_mod, "vote_entry", _fake_vote(entry_price=10_000, size_pct=1.0))
    monkeypatch.setattr("src.orchestrator.cash_deploy._KIS_GAP_SEC", 0)

    captured: dict = {}
    real_compute_capital_plan = cash_deploy_mod.compute_capital_plan

    def spy_compute_capital_plan(**kwargs):
        captured.update(kwargs)
        return real_compute_capital_plan(**kwargs)

    monkeypatch.setattr(cash_deploy_mod, "compute_capital_plan", spy_compute_capital_plan)

    repo = FakeRepo(pending=[_pending_position(10_000, 1_000)])  # 명목 10,000,000
    client = FakeClient(balance=_balance(cash=25_000_000, total_eval=30_000_000), deposit=25_000_000)
    sent_info, send_info = _noop_send()
    sent_warn, send_warning = _noop_send()

    run_cash_deploy(client, repo, _rules(), [_candidate(score=9)], {}, send_info, send_warning)

    assert captured["pending_notional_won"] == 10_000_000


def test_pending_positions_prevent_redeploy_that_would_otherwise_happen(monkeypatch):
    """D7 통합 증거 (관찰가능한 동작) — 완전히 같은 조건에서, PENDING 명목금액이 이미
    목표 가동률을 채울 만큼 크면 deployable_won 이 min_deploy_won 아래로 떨어져
    재배치가 나가지 않는다. PENDING이 없으면 같은 조건에서 재배치가 나간다 —
    이 차이가 없으면 30분 뒤 다음 틱이 이미 나간 미체결 주문의 현금을 다시 쓴다."""
    monkeypatch.setattr(entry_mod, "vote_entry", _fake_vote(entry_price=10_000, size_pct=1.0))
    monkeypatch.setattr("src.orchestrator.cash_deploy._KIS_GAP_SEC", 0)

    def _run(pending):
        repo = FakeRepo(pending=pending)
        client = FakeClient(balance=_balance(cash=25_000_000, total_eval=30_000_000), deposit=25_000_000)
        sent_info, send_info = _noop_send()
        sent_warn, send_warning = _noop_send()
        n = run_cash_deploy(client, repo, _rules(), [_candidate(score=9)], {}, send_info, send_warning)
        return n, client.submit_calls

    n_without, calls_without = _run([])
    # 26,600,000 명목 PENDING — 목표(target_utilization_pct=90% × 30,000,000=27,000,000)를
    # 거의 다 채워 gap_won 이 min_deploy_won(500,000) 아래로 떨어진다.
    n_with, calls_with = _run([_pending_position(10_000, 2_660)])

    assert n_without >= 1
    assert calls_without
    assert n_with == 0
    assert calls_with == []


def test_cash_deploy_disabled_returns_zero_and_skips_balance_lookup():
    repo = FakeRepo()
    client = FakeClient(balance=_balance())
    sent_info, send_info = _noop_send()
    sent_warn, send_warning = _noop_send()

    n = run_cash_deploy(client, repo, _rules(cash_deploy_enabled=False), [_candidate()], {}, send_info, send_warning)

    assert n == 0
    assert client.get_balance_calls == 0


def test_deployable_below_min_deploy_won_returns_zero_without_orders(monkeypatch):
    monkeypatch.setattr(entry_mod, "vote_entry", _fake_vote())
    monkeypatch.setattr("src.orchestrator.cash_deploy._KIS_GAP_SEC", 0)

    repo = FakeRepo()
    # 가동률이 이미 목표에 가까워 배치가능액이 min_deploy_won 미만이 되도록 설계
    client = FakeClient(
        balance=_balance(cash=1_000_000, total_eval=30_000_000, positions=[{"eval_amt": 26_000_000}]),
        deposit=1_000_000,
    )
    sent_info, send_info = _noop_send()
    sent_warn, send_warning = _noop_send()

    n = run_cash_deploy(client, repo, _rules(cash_deploy_underrun_warn_pct=0.0), [_candidate()], {}, send_info, send_warning)

    assert n == 0
    assert client.submit_calls == []


def test_no_candidates_returns_zero_and_warns_once(monkeypatch):
    monkeypatch.setattr(entry_mod, "vote_entry", _fake_vote())
    monkeypatch.setattr("src.orchestrator.cash_deploy._KIS_GAP_SEC", 0)

    repo = FakeRepo()
    client = FakeClient(balance=_balance(cash=25_000_000, total_eval=30_000_000), deposit=25_000_000)
    sent_info, send_info = _noop_send()
    sent_warn, send_warning = _noop_send()

    n = run_cash_deploy(client, repo, _rules(), [], {}, send_info, send_warning)

    assert n == 0
    assert len(sent_warn) == 1


def test_underrun_warning_sent_only_once_across_two_consecutive_calls(monkeypatch):
    monkeypatch.setattr(entry_mod, "vote_entry", _fake_vote())
    monkeypatch.setattr("src.orchestrator.cash_deploy._KIS_GAP_SEC", 0)

    repo = FakeRepo()
    client = FakeClient(balance=_balance(cash=25_000_000, total_eval=30_000_000), deposit=25_000_000)
    sent_info, send_info = _noop_send()
    sent_warn, send_warning = _noop_send()

    run_cash_deploy(client, repo, _rules(), [], {}, send_info, send_warning)
    run_cash_deploy(client, repo, _rules(), [], {}, send_info, send_warning)

    assert len(sent_warn) == 1


def test_no_warning_when_utilization_above_underrun_threshold(monkeypatch):
    """가동률이 underrun_warn_pct(70%)보다 실제로 높으면(83.3%) 후보가 없어도 경고하지 않는다."""
    monkeypatch.setattr(entry_mod, "vote_entry", _fake_vote())
    monkeypatch.setattr("src.orchestrator.cash_deploy._KIS_GAP_SEC", 0)

    repo = FakeRepo()
    # 투자중 25,000,000 / 총자산 30,000,000 → 가동률 83.3% > 70%
    client = FakeClient(
        balance=_balance(cash=5_000_000, total_eval=30_000_000, positions=[{"eval_amt": 25_000_000}]),
        deposit=5_000_000,
    )
    sent_info, send_info = _noop_send()
    sent_warn, send_warning = _noop_send()

    n = run_cash_deploy(client, repo, _rules(cash_deploy_underrun_warn_pct=70.0), [], {}, send_info, send_warning)

    assert n == 0
    assert sent_warn == []


def test_get_balance_none_returns_zero_and_warns_without_raising():
    repo = FakeRepo()
    client = FakeClient(balance=None)
    sent_info, send_info = _noop_send()
    sent_warn, send_warning = _noop_send()

    n = run_cash_deploy(client, repo, _rules(), [_candidate()], {}, send_info, send_warning)

    assert n == 0
    assert len(sent_warn) == 1


def test_get_deposit_exception_falls_back_to_balance_cash(monkeypatch):
    monkeypatch.setattr(entry_mod, "vote_entry", _fake_vote(entry_price=10_000, size_pct=1.0))
    monkeypatch.setattr("src.orchestrator.cash_deploy._KIS_GAP_SEC", 0)

    repo = FakeRepo()
    client = FakeClient(
        balance=_balance(cash=25_000_000, total_eval=30_000_000),
        deposit_error=RuntimeError("deposit lookup failed"),
    )
    sent_info, send_info = _noop_send()
    sent_warn, send_warning = _noop_send()

    n = run_cash_deploy(client, repo, _rules(), [_candidate(score=9)], {}, send_info, send_warning)

    assert n == 1
    assert not sent_warn


def test_submit_buy_market_closed_reject_does_not_warn(monkeypatch):
    monkeypatch.setattr(entry_mod, "vote_entry", _fake_vote(entry_price=10_000, size_pct=1.0))
    monkeypatch.setattr("src.orchestrator.cash_deploy._KIS_GAP_SEC", 0)

    repo = FakeRepo()
    client = FakeClient(
        balance=_balance(cash=25_000_000, total_eval=30_000_000),
        deposit=25_000_000,
        submit_accept=False,
        submit_msg1="장종료 시간외 거부",
    )
    sent_info, send_info = _noop_send()
    sent_warn, send_warning = _noop_send()

    n = run_cash_deploy(client, repo, _rules(), [_candidate(score=9)], {}, send_info, send_warning)

    assert n == 0
    assert sent_warn == []


def test_submit_buy_non_market_closed_reject_warns(monkeypatch):
    monkeypatch.setattr(entry_mod, "vote_entry", _fake_vote(entry_price=10_000, size_pct=1.0))
    monkeypatch.setattr("src.orchestrator.cash_deploy._KIS_GAP_SEC", 0)

    repo = FakeRepo()
    client = FakeClient(
        balance=_balance(cash=25_000_000, total_eval=30_000_000),
        deposit=25_000_000,
        submit_accept=False,
        submit_msg1="잔고 부족",
    )
    sent_info, send_info = _noop_send()
    sent_warn, send_warning = _noop_send()

    n = run_cash_deploy(client, repo, _rules(), [_candidate(score=9)], {}, send_info, send_warning)

    assert n == 0
    assert len(sent_warn) == 1


def test_overseas_candidates_produce_no_orders(monkeypatch):
    monkeypatch.setattr(entry_mod, "vote_entry", _fake_vote())
    monkeypatch.setattr("src.orchestrator.cash_deploy._KIS_GAP_SEC", 0)

    repo = FakeRepo()
    client = FakeClient(balance=_balance(cash=25_000_000, total_eval=30_000_000), deposit=25_000_000)
    sent_info, send_info = _noop_send()
    sent_warn, send_warning = _noop_send()

    n = run_cash_deploy(
        client, repo, _rules(), [_candidate("U1", asset_class="overseas_stock")], {}, send_info, send_warning,
    )

    assert n == 0
    assert client.submit_calls == []


def test_duplicate_ticker_excluded_from_candidates(monkeypatch):
    monkeypatch.setattr(entry_mod, "vote_entry", _fake_vote(entry_price=10_000, size_pct=1.0))
    monkeypatch.setattr("src.orchestrator.cash_deploy._KIS_GAP_SEC", 0)

    repo = FakeRepo(duplicates={"005930"})
    client = FakeClient(balance=_balance(cash=25_000_000, total_eval=30_000_000), deposit=25_000_000)
    sent_info, send_info = _noop_send()
    sent_warn, send_warning = _noop_send()

    n = run_cash_deploy(client, repo, _rules(), [_candidate("005930", score=9)], {}, send_info, send_warning)

    assert n == 0
    assert client.submit_calls == []


def test_candidates_capped_at_max_candidates_per_run(monkeypatch):
    monkeypatch.setattr(entry_mod, "vote_entry", _fake_vote(entry_price=10_000, size_pct=1.0))
    monkeypatch.setattr("src.orchestrator.cash_deploy._KIS_GAP_SEC", 0)

    repo = FakeRepo()
    client = FakeClient(balance=_balance(cash=25_000_000, total_eval=30_000_000), deposit=25_000_000)
    candidates = [_candidate(f"T{i}", score=i) for i in range(10)]
    sent_info, send_info = _noop_send()
    sent_warn, send_warning = _noop_send()

    n = run_cash_deploy(client, repo, _rules(), candidates, {}, send_info, send_warning)

    assert n == 4  # cash_deploy_max_candidates_per_run
    submitted_tickers = {t for t, *_ in client.submit_calls}
    assert submitted_tickers == {"T9", "T8", "T7", "T6"}


def test_morning_quota_exhausted_still_deploys_via_quota_override(monkeypatch):
    """D9 증거 — daily_entries_today=12(아침 예산 소진)여도 재배치가 진입한다."""
    monkeypatch.setattr(entry_mod, "vote_entry", _fake_vote(entry_price=10_000, size_pct=1.0))
    monkeypatch.setattr("src.orchestrator.cash_deploy._KIS_GAP_SEC", 0)

    repo = FakeRepo(today_entries=12)
    client = FakeClient(balance=_balance(cash=25_000_000, total_eval=30_000_000), deposit=25_000_000)
    sent_info, send_info = _noop_send()
    sent_warn, send_warning = _noop_send()

    n = run_cash_deploy(client, repo, _rules(max_daily_entries=12), [_candidate(score=9)], {}, send_info, send_warning)

    assert n == 1


# ---------------------------------------------------------------------------
# 태스크 06: `--deploy-cash` orchestrator __main__ 배선
#
# __main__.run()은 KIS와 파일시스템을 타므로, argparse와 조기 반환 분기만
# 검증한다. KisClient / Repo / run_cash_deploy / load_signal / latest_signal_date /
# resolve_signal_dir 를 monkeypatch 로 대체한다.
# ---------------------------------------------------------------------------

from pathlib import Path as _Path

from src.guardrails.rules import load_rules as _load_rules
from src.orchestrator import __main__ as main_mod


class _RecordingKisClient:
    """__main__.KisClient 대체 — 인스턴스 생성 여부/횟수만 기록한다."""
    created: list = []

    def __init__(self, mode="paper"):
        self.mode = mode
        _RecordingKisClient.created.append(self)

    def session(self):
        return self

    def __enter__(self):
        return SimpleNamespace()

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _reset_recording_kis_client():
    _RecordingKisClient.created = []
    yield
    _RecordingKisClient.created = []


def _spy_run_cash_deploy(calls: list, return_value: int = 3, raise_exc: Exception | None = None):
    def fn(client, repo, rules, candidates, signals, send_info, send_warning):
        calls.append({
            "client": client, "repo": repo, "rules": rules,
            "candidates": candidates, "signals": signals,
        })
        if raise_exc is not None:
            raise raise_exc
        return return_value

    return fn


def _raise_if_called(name: str):
    def fn(*args, **kwargs):
        raise AssertionError(f"{name} should not have been called")

    return fn


def test_parse_args_deploy_cash_flag_and_defaults():
    args = main_mod.parse_args(["--deploy-cash"])
    assert args.deploy_cash is True
    assert args.dip_only is False
    assert args.carry_over is False
    assert args.asset_class == "all"


def test_run_deploy_cash_calls_run_cash_deploy_exactly_once_with_loaded_rules(monkeypatch, tmp_path):
    calls: list = []
    signal_dir = tmp_path / "signals"
    signal_dir.mkdir()
    (signal_dir / "2026-08-12.json").write_text("{}")  # latest_signal_date 가 찾을 파일

    monkeypatch.setattr(main_mod, "KisClient", _RecordingKisClient)
    monkeypatch.setattr(main_mod, "Repo", FakeRepo)
    monkeypatch.setattr(main_mod, "run_cash_deploy", _spy_run_cash_deploy(calls, return_value=2))
    monkeypatch.setattr(main_mod, "resolve_signal_dir", lambda: signal_dir)
    monkeypatch.setattr(main_mod, "latest_signal_date", lambda d, suffix: __import__("datetime").date(2026, 8, 12))

    signals = {"buys": [{"ticker": "005930", "name": "삼성전자", "score": 9}]}
    monkeypatch.setattr(main_mod, "load_signal", lambda **kwargs: signals)

    n = main_mod.run(["--deploy-cash"])

    assert n == 0  # --deploy-cash 경로는 항상 0 반환 (launchd 비정상 종료 방지)
    assert len(calls) == 1
    rules = _load_rules(_Path("config/trading_rules.yaml"))
    assert calls[0]["rules"] == rules
    from src.orchestrator.signal_loader import filter_buy_candidates
    assert calls[0]["candidates"] == filter_buy_candidates(signals, min_score=rules.entry_signal_score_min)


def test_deploy_cash_path_does_not_call_select_entries(monkeypatch, tmp_path):
    signal_dir = tmp_path / "signals"
    signal_dir.mkdir()

    monkeypatch.setattr(main_mod, "KisClient", _RecordingKisClient)
    monkeypatch.setattr(main_mod, "Repo", FakeRepo)
    monkeypatch.setattr(main_mod, "run_cash_deploy", _spy_run_cash_deploy([]))
    monkeypatch.setattr(main_mod, "resolve_signal_dir", lambda: signal_dir)
    monkeypatch.setattr(main_mod, "latest_signal_date", lambda d, suffix: __import__("datetime").date(2026, 8, 12))
    monkeypatch.setattr(main_mod, "load_signal", lambda **kwargs: {"buys": []})
    monkeypatch.setattr(main_mod, "select_entries", _raise_if_called("select_entries"))

    n = main_mod.run(["--deploy-cash"])
    assert n == 0


def test_morning_path_does_not_call_run_cash_deploy(monkeypatch):
    monkeypatch.setattr(main_mod, "run_cash_deploy", _raise_if_called("run_cash_deploy"))
    # buys=[] 로 조기 반환시켜 KIS/DB 를 건드리지 않고도 아침 경로가 run_cash_deploy 를
    # 호출하지 않음을 검증한다.
    monkeypatch.setattr(main_mod, "load_signal", lambda **kwargs: {"buys": []})

    n = main_mod.run([])
    assert n == 0


def test_cash_deploy_disabled_skips_kis_client_and_run_cash_deploy(monkeypatch):
    disabled_rules = _load_rules(_Path("config/trading_rules.yaml"))
    disabled_rules = disabled_rules.__class__(**{**disabled_rules.__dict__, "cash_deploy_enabled": False})

    monkeypatch.setattr(main_mod, "load_rules", lambda path: disabled_rules)
    monkeypatch.setattr(main_mod, "KisClient", _RecordingKisClient)
    monkeypatch.setattr(main_mod, "run_cash_deploy", _raise_if_called("run_cash_deploy"))

    n = main_mod.run(["--deploy-cash"])

    assert n == 0
    assert _RecordingKisClient.created == []


def test_deploy_cash_no_signal_file_returns_zero(monkeypatch):
    monkeypatch.setattr(main_mod, "resolve_signal_dir", lambda: _Path("data/signals-unused"))
    monkeypatch.setattr(main_mod, "latest_signal_date", lambda d, suffix: None)
    monkeypatch.setattr(main_mod, "run_cash_deploy", _raise_if_called("run_cash_deploy"))

    n = main_mod.run(["--deploy-cash"])
    assert n == 0


def test_deploy_cash_signal_load_failure_returns_zero(monkeypatch):
    monkeypatch.setattr(main_mod, "resolve_signal_dir", lambda: _Path("data/signals-unused"))
    monkeypatch.setattr(main_mod, "latest_signal_date", lambda d, suffix: __import__("datetime").date(2026, 8, 12))
    monkeypatch.setattr(main_mod, "load_signal", lambda **kwargs: None)
    monkeypatch.setattr(main_mod, "run_cash_deploy", _raise_if_called("run_cash_deploy"))

    n = main_mod.run(["--deploy-cash"])
    assert n == 0


def test_run_cash_deploy_exception_is_swallowed_and_returns_zero(monkeypatch, tmp_path):
    signal_dir = tmp_path / "signals"
    signal_dir.mkdir()

    monkeypatch.setattr(main_mod, "KisClient", _RecordingKisClient)
    monkeypatch.setattr(main_mod, "Repo", FakeRepo)
    monkeypatch.setattr(main_mod, "resolve_signal_dir", lambda: signal_dir)
    monkeypatch.setattr(main_mod, "latest_signal_date", lambda d, suffix: __import__("datetime").date(2026, 8, 12))
    monkeypatch.setattr(main_mod, "load_signal", lambda **kwargs: {"buys": [{"ticker": "005930", "name": "삼성", "score": 9}]})
    monkeypatch.setattr(main_mod, "run_cash_deploy", _spy_run_cash_deploy([], raise_exc=RuntimeError("boom")))

    n = main_mod.run(["--deploy-cash"])
    assert n == 0


def test_deploy_cash_flag_exists_in_argparse_without_system_exit():
    args = main_mod.parse_args(["--deploy-cash"])
    assert args.deploy_cash is True


def test_existing_dip_only_and_carry_over_and_asset_class_flags_unchanged():
    args = main_mod.parse_args(["--dip-only"])
    assert args.dip_only is True
    assert args.deploy_cash is False

    args = main_mod.parse_args(["--carry-over"])
    assert args.carry_over is True
    assert args.deploy_cash is False

    args = main_mod.parse_args(["--asset-class", "overseas_stock"])
    assert args.asset_class == "overseas_stock"
    assert args.deploy_cash is False


# ===========================================================================
# 2026-08-13 실장애 회귀 — 낡은 신호가로 SL/TP 산출 + 동일 종목 반복 매수
# ===========================================================================

def test_pick_candidates_excludes_ticker_closed_today():
    """당일 청산 종목은 다음 틱에서 재매수 후보가 아니다 (1주 왕복 6회 재발 방지)."""
    repo = FakeRepo(closed_today={"000660"})
    picked = pick_candidates(
        [_candidate("000660", "SK하이닉스", score=9), _candidate("005930", "삼성전자", score=8)],
        repo, 4,
    )
    assert [c["ticker"] for c in picked] == ["005930"]


def test_pick_candidates_keeps_ticker_closed_on_another_day():
    """당일이 아닌 청산은 막지 않는다 — get_tickers_closed_today 가 오늘만 준다."""
    repo = FakeRepo(closed_today=set())
    picked = pick_candidates([_candidate("000660", "SK하이닉스")], repo, 4)
    assert [c["ticker"] for c in picked] == ["000660"]


def test_fetch_live_quotes_returns_live_quotes():
    client = FakeClient(_balance(), quotes={"005930": 71_200, "000660": 1_612_000})
    q = fetch_live_quotes(client, [_candidate("005930"), _candidate("000660")])
    assert {t: v.current_price for t, v in q.items()} == {"005930": 71_200, "000660": 1_612_000}
    assert client.quote_calls == ["005930", "000660"]


def test_fetch_live_quotes_drops_candidate_on_quote_failure():
    """시세를 못 얻으면 낡은 종가로 넘어가지 않고 dict 에서 빠진다."""
    client = FakeClient(_balance(), quotes={"005930": None, "000660": 1_612_000})
    q = fetch_live_quotes(client, [_candidate("005930"), _candidate("000660")])
    assert {t: v.current_price for t, v in q.items()} == {"000660": 1_612_000}


def test_fetch_live_quotes_drops_candidate_on_zero_price():
    client = FakeClient(_balance(), quotes={"005930": 0})
    assert fetch_live_quotes(client, [_candidate("005930")]) == {}


def test_fetch_live_quotes_survives_quote_exception():
    client = FakeClient(_balance(), quote_error=RuntimeError("boom"))
    assert fetch_live_quotes(client, [_candidate("005930")]) == {}


def test_fetch_live_quotes_empty_input():
    client = FakeClient(_balance())
    assert fetch_live_quotes(client, []) == {}


def test_run_cash_deploy_prices_order_from_live_quote_not_stale_close(monkeypatch):
    """실장애 재현 회귀 — 신호 1,504,000 / 실제 1,612,000 이면 주문가는 실제 쪽이어야 한다.

    낡은 값으로 잡으면 TP(1,572,000)가 체결가보다 낮아 포지션이 태어나자마자 청산된다.
    """
    monkeypatch.setattr(entry_mod, "vote_entry", _fake_vote(
        entry_price=1_504_000, size_pct=15.0, stop_loss_pct=2.5, take_profit_pct=4.5))
    client = FakeClient(_balance(cash=25_000_000, total_eval=30_000_000),
                        deposit=25_000_000, quotes={"000660": 1_612_000})
    repo = FakeRepo()
    sent: list[str] = []
    n = run_cash_deploy(
        client, repo, _rules(), [_candidate("000660", "SK하이닉스",
                                            panel_summary={"last_close": 1_504_000})],
        {}, sent.append, sent.append,
    )
    assert n == 1
    ticker, qty, price, order_type = client.submit_calls[0]
    assert ticker == "000660"
    assert price >= 1_612_000, f"주문가가 낡은 종가 기준이다: {price}"
    pos = repo.inserted[0]
    assert pos["take_profit"] > price, (
        f"익절가({pos['take_profit']})가 체결 기준가({price}) 이하 — 즉시 청산된다"
    )
    assert pos["stop_loss"] < price


def test_run_cash_deploy_skips_candidate_without_quote(monkeypatch):
    monkeypatch.setattr(entry_mod, "vote_entry", _fake_vote())
    client = FakeClient(_balance(), deposit=25_000_000, quotes={"005930": None})
    repo = FakeRepo()
    sent: list[str] = []
    n = run_cash_deploy(client, repo, _rules(), [_candidate("005930")], {},
                        sent.append, sent.append)
    assert n == 0
    assert client.submit_calls == []


def test_run_cash_deploy_refreshes_prompt_close_with_live_price(monkeypatch):
    """프롬프트의 종가도 실시간으로 갱신된다 — 논리와 체결가가 갈라지지 않게."""
    seen: dict[str, str] = {}

    def spy_vote(prompt, n, timeout):
        seen["prompt"] = prompt
        return _fake_vote(entry_price=1_504_000)(prompt, n, timeout)

    monkeypatch.setattr(entry_mod, "vote_entry", spy_vote)
    client = FakeClient(_balance(), deposit=25_000_000, quotes={"000660": 1_612_000})
    run_cash_deploy(client, FakeRepo(), _rules(),
                    [_candidate("000660", "SK하이닉스",
                                panel_summary={"last_close": 1_504_000})],
                    {}, lambda m: None, lambda m: None)
    assert "1,612,000" in seen["prompt"]
    assert "1,504,000" not in seen["prompt"]


def test_refresh_panel_updates_both_price_and_change_pct():
    """last_close 만 갈고 d_change_pct 를 두면 현재가 옆에 전일 등락률이 붙는다."""
    c = _candidate("000660", panel_summary={"last_close": 1_504_000, "d_change_pct": -0.3,
                                            "vol_ratio_5d": 1.2})
    refresh_panel(c, SimpleNamespace(current_price=1_612_000, d_change_pct=7.18))
    assert c["panel_summary"]["last_close"] == 1_612_000
    assert c["panel_summary"]["d_change_pct"] == 7.18
    assert c["panel_summary"]["vol_ratio_5d"] == 1.2, "무관한 필드는 보존"


def test_refresh_panel_keeps_stale_change_when_quote_lacks_it():
    c = _candidate("000660", panel_summary={"last_close": 1_504_000, "d_change_pct": -0.3})
    refresh_panel(c, SimpleNamespace(current_price=1_612_000))
    assert c["panel_summary"]["last_close"] == 1_612_000
    assert c["panel_summary"]["d_change_pct"] == -0.3


def test_refresh_panel_on_candidate_without_panel():
    c = _candidate("000660")
    c.pop("panel_summary", None)
    refresh_panel(c, SimpleNamespace(current_price=1_000, d_change_pct=1.0))
    assert c["panel_summary"] == {"last_close": 1_000, "d_change_pct": 1.0}


def test_run_cash_deploy_prompt_shows_live_change_pct_not_stale(monkeypatch):
    """프롬프트의 등락률이 실시간 값이어야 한다 — 현재가와 짝이 맞아야 한다."""
    seen: dict[str, str] = {}

    def spy_vote(prompt, n, timeout):
        seen["prompt"] = prompt
        return _fake_vote(entry_price=1_504_000)(prompt, n, timeout)

    monkeypatch.setattr(entry_mod, "vote_entry", spy_vote)
    client = FakeClient(_balance(), deposit=25_000_000,
                        quotes={"000660": 1_612_000}, d_change={"000660": 7.18})
    run_cash_deploy(client, FakeRepo(), _rules(),
                    [_candidate("000660", "SK하이닉스",
                                panel_summary={"last_close": 1_504_000, "d_change_pct": -0.30})],
                    {}, lambda m: None, lambda m: None)
    assert "+7.18%" in seen["prompt"]
    assert "-0.30%" not in seen["prompt"]
