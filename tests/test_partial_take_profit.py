# -*- coding: utf-8 -*-
"""부분 익절(partial take-profit) 테스트.

정책(2026-07-06): TP 1차 도달 시 sell_pct만 매도해 실익 확정, 잔여분은
TP measured-move 연장 + 손절 본전 상향. 포지션당 1회, 1주면 전량 익절.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from src.monitor.enforcement import held_qty, plan_partial_take_profit
from src.storage.models import Position, create_all
from src.storage.repository import Repo


def _get(repo: Repo, pos_id: int) -> Position:
    with repo.SessionLocal() as s:
        return s.execute(select(Position).where(Position.id == pos_id)).scalar_one()


@dataclass
class _Pos:
    entry_price_actual: int = 100_000
    entry_price_target: int = 100_000
    entry_qty: int = 10
    current_take_profit: int = 110_000
    qty_remaining: int | None = None
    partial_exit_count: int = 0
    ticker: str = "000000"


@dataclass
class _Rules:
    partial_tp_enabled: bool = True
    partial_tp_sell_pct: float = 50.0
    partial_tp_extension: float = 0.5
    partial_tp_breakeven_stop: bool = True


# ============================================================
# plan_partial_take_profit — 순수 판단 로직
# ============================================================

def test_plan_basic_half_sell_and_extension():
    plan = plan_partial_take_profit(_Pos(), _Rules())
    assert plan is not None
    assert plan.sell_qty == 5
    assert plan.remain_qty == 5
    # TP 연장: 110,000 + (110,000-100,000)×0.5 = 115,000
    assert plan.new_take_profit == 115_000
    # 손절 본전 상향
    assert plan.new_stop_loss == 100_000


def test_plan_odd_qty_floor():
    plan = plan_partial_take_profit(_Pos(entry_qty=5), _Rules())
    assert plan is not None
    assert plan.sell_qty == 2   # floor(5×0.5)
    assert plan.remain_qty == 3


def test_plan_single_share_full_exit():
    assert plan_partial_take_profit(_Pos(entry_qty=1), _Rules()) is None


def test_plan_disabled():
    assert plan_partial_take_profit(_Pos(), _Rules(partial_tp_enabled=False)) is None


def test_plan_only_once_per_position():
    assert plan_partial_take_profit(_Pos(partial_exit_count=1), _Rules()) is None


def test_plan_uses_remaining_qty_after_partial():
    # 부분 매도 후 잔여 3주 상태에서 held_qty가 잔여를 반환
    pos = _Pos(entry_qty=10, qty_remaining=3)
    assert held_qty(pos) == 3


def test_plan_no_breakeven_stop_option():
    plan = plan_partial_take_profit(_Pos(), _Rules(partial_tp_breakeven_stop=False))
    assert plan is not None
    assert plan.new_stop_loss is None


def test_plan_rejects_tp_below_entry():
    # TP가 진입가 이하인 비정상 상태 → 부분 익절 없이 기존 경로(전량)로
    assert plan_partial_take_profit(_Pos(current_take_profit=99_000), _Rules()) is None


# ============================================================
# Repo — 부분 익절 반영 + 최종 청산 PnL 합산
# ============================================================

def _repo(tmp_path) -> Repo:
    repo = Repo(tmp_path / "test.sqlite")
    create_all(repo.engine)
    return repo


def _insert(repo: Repo) -> int:
    return repo.insert_position(
        ticker="000270", name="기아", signal_score=5, confidence=5,
        broker_order_id="0001", strategy="MARKET_OPEN", price_target=100_000,
        qty=10, thesis="t", watch_signals=[], stop_loss=97_500,
        take_profit=110_000, max_hold_days=5,
    )


def test_repo_partial_then_close_pnl(tmp_path):
    repo = _repo(tmp_path)
    pos_id = _insert(repo)

    # 부분 익절: 5주 @110,000 (진입 100,000) → 실현 +50,000
    repo.apply_partial_exit(pos_id, sold_qty=5, sell_price=110_000,
                            new_take_profit=115_000, new_stop_loss=100_000)
    pos = _get(repo, pos_id)
    assert pos.qty_remaining == 5
    assert pos.partial_exit_count == 1
    assert pos.partial_realized_pnl_won == 50_000
    assert pos.current_take_profit == 115_000
    assert pos.current_stop_loss == 100_000
    assert pos.status != "CLOSED"   # 포지션 유지

    # 최종 청산: 잔여 5주 @115,000 → +75,000, 총 pnl = 50,000+75,000
    repo.close_position(pos_id, "TAKE_PROFIT", 115_000, "0002")
    p = _get(repo, pos_id)
    assert p.status == "CLOSED"
    assert p.pnl_won == 125_000
    # pnl_pct는 총 매입금액(100만) 대비: 12.5%
    assert abs(p.pnl_pct - 12.5) < 0.01


def test_repo_close_without_partial_unchanged(tmp_path):
    # 부분 익절 없던 포지션의 기존 PnL 계산 회귀 확인
    repo = _repo(tmp_path)
    pos_id = _insert(repo)
    repo.close_position(pos_id, "STOP_LOSS", 97_500, "0002")
    p = _get(repo, pos_id)
    assert p.pnl_won == (97_500 - 100_000) * 10
    assert abs(p.pnl_pct - (-2.5)) < 0.01


def test_repo_partial_stop_loss_never_lowered(tmp_path):
    # 본전 상향이 기존 손절가보다 낮으면(이미 더 높게 조여진 상태) 유지
    repo = _repo(tmp_path)
    pos_id = _insert(repo)
    repo.update_stop_loss(pos_id, 105_000)  # LLM이 이미 105,000까지 조임
    repo.apply_partial_exit(pos_id, sold_qty=5, sell_price=110_000,
                            new_take_profit=115_000, new_stop_loss=100_000)
    p = _get(repo, pos_id)
    assert p.current_stop_loss == 105_000   # 하향 금지
