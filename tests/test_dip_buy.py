"""dip_buy 순수 로직 테스트 (compute_drop_pct / select_tranche) + run_dip_buy 일괄 매수."""
from dataclasses import dataclass, field

import pytest

from src.orchestrator.dip_buy import (
    compute_drop_pct,
    is_market_closed_reject,
    run_dip_buy,
    select_tranche,
)

TRANCHES = [
    {"drop_pct": -3.0, "size_pct": 2.0},
    {"drop_pct": -6.0, "size_pct": 3.0},
    {"drop_pct": -10.0, "size_pct": 5.0},
]
MAX_EXP = 15.0


def test_compute_drop_pct_basic():
    # 6 closes, window=5: last/first-1
    assert compute_drop_pct([100, 99, 98, 97, 96, 90], 5) == pytest.approx(-10.0)
    assert compute_drop_pct([100, 100, 100, 100, 100, 95], 5) == pytest.approx(-5.0)


def test_compute_drop_pct_insufficient_or_bad():
    assert compute_drop_pct([], 5) is None
    assert compute_drop_pct([100, 99], 5) is None          # len <= window
    assert compute_drop_pct([0, 0, 0, 0, 0, 0], 5) is None  # base <= 0


def test_select_tranche_below_threshold_returns_none():
    assert select_tranche(-1.0, TRANCHES, 0, MAX_EXP) is None   # not deep enough
    assert select_tranche(0.5, TRANCHES, 0, MAX_EXP) is None    # up day


def test_select_tranche_first_dip_buys_first():
    tr = select_tranche(-3.5, TRANCHES, 0, MAX_EXP)
    assert tr is not None and tr["drop_pct"] == -3.0 and tr["size_pct"] == 2.0


def test_select_tranche_no_double_fill_same_level():
    # -3.5% qualifies only tranche1; if already filled 1 → nothing more yet
    assert select_tranche(-3.5, TRANCHES, 1, MAX_EXP) is None


def test_select_tranche_deepening_buys_next():
    # dropped to -7% (qualifies 1st+2nd), 1 already filled → buy 2nd
    tr = select_tranche(-7.0, TRANCHES, 1, MAX_EXP)
    assert tr is not None and tr["drop_pct"] == -6.0

    # -11% qualifies all 3; 2 filled → buy 3rd
    tr3 = select_tranche(-11.0, TRANCHES, 2, MAX_EXP)
    assert tr3 is not None and tr3["drop_pct"] == -10.0


def test_select_tranche_all_filled_returns_none():
    assert select_tranche(-20.0, TRANCHES, 3, MAX_EXP) is None  # no tranche left


def test_select_tranche_exposure_cap():
    # cap below 3rd tranche cumulative (2+3+5=10): cap=9 blocks the 3rd
    assert select_tranche(-11.0, TRANCHES, 2, 9.0) is None
    # cap=10 allows the 3rd (cumulative exactly 10)
    assert select_tranche(-11.0, TRANCHES, 2, 10.0) is not None


def test_select_tranche_recovery_holds():
    # recovered to -1% but 2 tranches still open → no new buy (hold existing)
    assert select_tranche(-1.0, TRANCHES, 2, MAX_EXP) is None


def test_is_market_closed_reject():
    # 시간외/장종료 류 → True (경고 skip 대상)
    assert is_market_closed_reject("모의투자 장종료 입니다.") is True
    assert is_market_closed_reject("장개시 전입니다") is True
    assert is_market_closed_reject("거래시간이 아닙니다") is True
    # 진짜 문제 → False (경고 보냄)
    assert is_market_closed_reject("주문가능금액이 부족합니다") is False
    assert is_market_closed_reject("종목코드 오류") is False
    assert is_market_closed_reject("") is False


# ============================================================
# run_dip_buy — 낙폭 도달 단계 일괄 매수 (2026-07-06)
# ============================================================

@dataclass
class _Quote:
    current_price: int = 10_000


@dataclass
class _Balance:
    cash: int = 30_000_000


@dataclass
class _OrderResult:
    accepted: bool = True
    broker_order_id: str = "0001"
    raw: dict = field(default_factory=dict)


class _FakeClient:
    def __init__(self, closes, reject_after: int | None = None):
        self._closes = closes
        self._reject_after = reject_after
        self.buys: list[tuple[str, int, int]] = []

    def get_balance(self):
        return _Balance()

    def get_daily_closes(self, etf, n):
        return self._closes

    def get_quote(self, etf):
        return _Quote()

    def submit_buy(self, etf, qty, price):
        if self._reject_after is not None and len(self.buys) >= self._reject_after:
            return _OrderResult(accepted=False, raw={"msg1": "주문가능금액이 부족합니다"})
        self.buys.append((etf, qty, price))
        return _OrderResult()


class _FakeRepo:
    def __init__(self):
        self.inserted: list[dict] = []

    def count_open_dip_positions(self, etf):
        return 0

    def insert_position(self, **kw):
        self.inserted.append(kw)
        return len(self.inserted)


class _Rules:
    dip_buy = {
        "enabled": True,
        "window_days": 5,
        "index_etf": {"kospi": "069500"},
        "tranches": [
            {"drop_pct": -3.0, "size_pct": 5.0},
            {"drop_pct": -6.0, "size_pct": 7.5},
            {"drop_pct": -10.0, "size_pct": 10.0},
        ],
        "max_total_exposure_pct": 25.0,
        "stop_loss_pct": 8.0,
        "take_profit_pct": 5.0,
        "max_hold_days": 20,
    }


def _run(client, monkeypatch):
    monkeypatch.setattr("src.orchestrator.dip_buy.time.sleep", lambda s: None)
    repo = _FakeRepo()
    n = run_dip_buy(client, repo, _Rules(), lambda m: None, lambda m: None)
    return n, client, repo


def test_run_deep_drop_fills_all_qualified_tranches(monkeypatch):
    # 5d -11.5% → 3단계 전부 자격 → 한 실행에서 3건 일괄 매수
    client = _FakeClient(closes=[11300, 11000, 10800, 10500, 10200, 10000])  # -11.5%
    n, client, repo = _run(client, monkeypatch)
    assert n == 3
    qtys = [q for _, q, _ in client.buys]
    assert qtys == [150, 225, 300]   # 3000만 × 5/7.5/10% ÷ 10,000원
    assert [p["features"]["tranche"] for p in repo.inserted] == [1, 2, 3]


def test_run_shallow_drop_fills_only_first(monkeypatch):
    # 5d -4% → 1단계만
    client = _FakeClient(closes=[10420, 10300, 10200, 10100, 10050, 10000])  # -4.0%
    n, client, repo = _run(client, monkeypatch)
    assert n == 1
    assert [p["features"]["tranche"] for p in repo.inserted] == [1]


def test_run_reject_stops_loop_without_further_orders(monkeypatch):
    # 깊은 낙폭이지만 2번째 주문이 거부되면 루프 중단 (1건만 기록)
    client = _FakeClient(closes=[11300, 11000, 10800, 10500, 10200, 10000],
                         reject_after=1)
    n, client, repo = _run(client, monkeypatch)
    assert n == 1
    assert len(repo.inserted) == 1


def test_run_no_drop_no_buy(monkeypatch):
    client = _FakeClient(closes=[10000, 10000, 10000, 10000, 10000, 10100])  # +1%
    n, client, repo = _run(client, monkeypatch)
    assert n == 0
    assert client.buys == []
