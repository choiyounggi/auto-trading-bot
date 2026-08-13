"""자본 배치 계획 — 총자산·투자중·가동률·배치가능액 계산.

KIS 네트워크 호출을 하지 않는다. 순수 계산만 하고, 값은 호출자가 넣어준다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.guardrails.rules import TradingRules

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapitalPlan:
    total_asset_won: int      # D1
    invested_won: int         # D3 + D7 (보유 평가액 + PENDING 명목)
    buying_power_won: int     # D2
    utilization_pct: float    # invested / total_asset × 100
    target_pct: float
    deployable_won: int       # 이번에 새로 투입 가능한 금액
    buffer_won: int           # 남겨둘 현금


def compute_capital_plan(
    *,
    total_asset_won: int,
    position_eval_won: int,
    pending_notional_won: int,
    buying_power_won: int,
    rules: TradingRules,
) -> CapitalPlan:
    invested_won = position_eval_won + pending_notional_won
    utilization_pct = (invested_won / total_asset_won * 100) if total_asset_won else 0.0
    buffer_won = int(total_asset_won * rules.cash_buffer_pct / 100)
    target_won = int(total_asset_won * rules.target_utilization_pct / 100)
    gap_won = target_won - invested_won
    spendable_won = buying_power_won - buffer_won
    deployable_won = max(0, min(gap_won, spendable_won))

    plan = CapitalPlan(
        total_asset_won=total_asset_won,
        invested_won=invested_won,
        buying_power_won=buying_power_won,
        utilization_pct=utilization_pct,
        target_pct=rules.target_utilization_pct,
        deployable_won=deployable_won,
        buffer_won=buffer_won,
    )
    log.info(
        "capital plan: total=%d invested=%d(%.1f%%) buying_power=%d deployable=%d",
        plan.total_asset_won, plan.invested_won, plan.utilization_pct,
        plan.buying_power_won, plan.deployable_won,
    )
    return plan


def position_eval_won(positions: list[dict]) -> int:
    """보유 종목 평가금액 합. 'eval_amt' 키가 없으면 0으로 센다."""
    return sum(p.get("eval_amt", 0) for p in positions)
