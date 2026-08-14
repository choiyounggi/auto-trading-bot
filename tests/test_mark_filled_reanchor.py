"""Repo.mark_filled — 체결가 기준 SL/TP 재고정(re-anchor).

2026-08-13/14 실장애 회귀: 발주 전 기준가로 산출된 TP 절대가가 시장가 체결 후에도
그대로 남아, 체결가 대비 +0.06%~+0.42% 극소익에서 TAKE_PROFIT이 발동했다.
mark_filled가 목표가 대비 비율을 보존해 체결가 기준으로 재고정해야 한다.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from src.storage.models import Position, create_all
from src.storage.repository import Repo


def _repo(tmp_path) -> Repo:
    repo = Repo(tmp_path / "trades.sqlite")
    create_all(repo.engine)
    return repo


def _insert_pending(repo: Repo, **kwargs) -> int:
    defaults = dict(
        ticker="000660",
        name="SK하이닉스",
        status="PENDING",
        entry_price_target=1_655_000,
        entry_qty=1,
        entry_at=datetime(2026, 8, 14, 9, 30, 0),
        current_stop_loss=1_614_000,   # -2.48%
        current_take_profit=1_688_000, # +1.99%
    )
    defaults.update(kwargs)
    with Session(repo.engine) as s:
        pos = Position(**defaults)
        s.add(pos)
        s.commit()
        return pos.id


def _get(repo: Repo, pos_id: int) -> Position:
    with Session(repo.engine) as s:
        return s.get(Position, pos_id)


def test_fill_above_target_reanchors_tp_and_sl_preserving_pct(tmp_path):
    """실장애 사례: 기준가 1,655,000 → 시장가 체결 1,682,000 (+1.6% 슬리피지).

    재고정 전에는 TP까지 남은 폭이 0.36%뿐 — 수수료도 못 건지는 익절.
    재고정 후에는 체결가 대비 원래 비율(±%)이 보존되어야 한다.
    """
    repo = _repo(tmp_path)
    pos_id = _insert_pending(repo)

    repo.mark_filled(pos_id, 1_682_000)

    pos = _get(repo, pos_id)
    assert pos.status == "OPEN"
    assert pos.entry_price_actual == 1_682_000
    ratio = 1_682_000 / 1_655_000
    assert pos.current_take_profit == int(round(1_688_000 * ratio))
    assert pos.current_stop_loss == int(round(1_614_000 * ratio))
    # 체결가 대비 TP 폭이 원래 설계(+1.99%)대로 복원됐는지 — 극소익 익절 차단의 핵심.
    tp_margin_pct = (pos.current_take_profit / 1_682_000 - 1) * 100
    assert tp_margin_pct > 1.9


def test_fill_below_target_scales_down_keeping_pct(tmp_path):
    repo = _repo(tmp_path)
    pos_id = _insert_pending(repo)

    repo.mark_filled(pos_id, 1_600_000)

    pos = _get(repo, pos_id)
    ratio = 1_600_000 / 1_655_000
    assert pos.current_take_profit == int(round(1_688_000 * ratio))
    assert pos.current_stop_loss == int(round(1_614_000 * ratio))


def test_fill_equal_to_target_leaves_prices_unchanged(tmp_path):
    repo = _repo(tmp_path)
    pos_id = _insert_pending(repo)

    repo.mark_filled(pos_id, 1_655_000)

    pos = _get(repo, pos_id)
    assert pos.status == "OPEN"
    assert pos.current_take_profit == 1_688_000
    assert pos.current_stop_loss == 1_614_000


def test_zero_actual_price_marks_open_without_reanchor(tmp_path):
    """경계값: 브로커 avg_price가 0으로 오면 재고정하지 않고 기존 값 유지."""
    repo = _repo(tmp_path)
    pos_id = _insert_pending(repo)

    repo.mark_filled(pos_id, 0)

    pos = _get(repo, pos_id)
    assert pos.status == "OPEN"
    assert pos.entry_price_actual == 0
    assert pos.current_take_profit == 1_688_000
    assert pos.current_stop_loss == 1_614_000


def test_missing_target_price_skips_reanchor(tmp_path):
    repo = _repo(tmp_path)
    pos_id = _insert_pending(repo, entry_price_target=None)

    repo.mark_filled(pos_id, 1_682_000)

    pos = _get(repo, pos_id)
    assert pos.status == "OPEN"
    assert pos.entry_price_actual == 1_682_000
    assert pos.current_take_profit == 1_688_000
    assert pos.current_stop_loss == 1_614_000


def test_null_sl_tp_do_not_crash(tmp_path):
    repo = _repo(tmp_path)
    pos_id = _insert_pending(repo, current_stop_loss=None, current_take_profit=None)

    repo.mark_filled(pos_id, 1_682_000)

    pos = _get(repo, pos_id)
    assert pos.status == "OPEN"
    assert pos.current_stop_loss is None
    assert pos.current_take_profit is None


def test_unknown_position_id_is_noop(tmp_path):
    repo = _repo(tmp_path)
    repo.mark_filled(99_999, 1_682_000)  # 예외 없이 조용히 통과해야 한다


def test_reconcile_pending_from_balance_reanchors_via_avg_price(tmp_path):
    """모니터 30분 틱 경로: KIS 잔고 avg_price로 체결 확정 시에도 재고정."""
    repo = _repo(tmp_path)
    pos_id = _insert_pending(repo)

    filled, cancelled = repo.reconcile_pending_from_balance(
        [{"ticker": "000660", "qty": 1, "avg_price": 1_682_000}]
    )

    assert (filled, cancelled) == (1, 0)
    pos = _get(repo, pos_id)
    ratio = 1_682_000 / 1_655_000
    assert pos.current_take_profit == int(round(1_688_000 * ratio))
