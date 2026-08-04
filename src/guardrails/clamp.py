"""LLM 출력 강제 보정 — 절대 한도 적용."""
from __future__ import annotations

from src.guardrails.rules import TradingRules
from src.llm.schemas import EntryDecision, MonitorDecision


def clamp_entry(decision: EntryDecision, rules: TradingRules) -> EntryDecision:
    """
    LLM 진입 결정에 가드레일 적용.
    - confidence < entry_min_confidence → SKIP
    - size_pct/stop_loss_pct/take_profit_pct/max_hold_days → max로 clamp
    - stop_loss_pct < min_stop_loss_pct → min으로 올림
    """
    if decision.action == "SKIP":
        return decision

    if decision.confidence < rules.entry_min_confidence:
        return decision.model_copy(update={"action": "SKIP"})

    # BUY 결정인데 None 필드 있으면 default로 — 단 entry_price=0이면 의미 없음 → SKIP
    if not decision.entry_price or not decision.size_pct:
        return decision.model_copy(update={"action": "SKIP"})

    return decision.model_copy(update={
        "size_pct": max(
            min(decision.size_pct, rules.max_size_pct),
            rules.min_size_pct,
        ),
        "stop_loss_pct": max(
            min(decision.stop_loss_pct or rules.min_stop_loss_pct, rules.max_stop_loss_pct),
            rules.min_stop_loss_pct,
        ),
        "take_profit_pct": max(
            min(decision.take_profit_pct or rules.min_take_profit_pct, rules.max_take_profit_pct),
            rules.min_take_profit_pct,
        ),
        "max_hold_days": min(decision.max_hold_days or rules.max_hold_days, rules.max_hold_days),
    })


def validate_monitor(
    decision: MonitorDecision,
    current_stop: int,
    current_tp: int,
    current_price: int,
    entry_price: int,
    rules: TradingRules,
) -> tuple[MonitorDecision, list[str]]:
    """
    동적 조정 결정 안전 규칙 적용. 위반 시 HOLD로 강제.
    Returns (final_decision, violation_log).

    안전 규칙:
    1. TIGHTEN_STOP은 new_stop_loss >= current_stop 만 인정 (하향 금지)
    2. RAISE_TP는 new_take_profit > current_tp 만 인정 + entry × 1.10 cap
    3. RAISE_TP는 new_take_profit > current_price 만 인정 (이미 도달 후 상향 금지)
    4. CLOSE_NOW는 confidence >= close_now_min_confidence 만 인정
    """
    violations: list[str] = []

    if decision.action == "HOLD":
        return decision, violations

    if decision.action == "TIGHTEN_STOP":
        if decision.new_stop_loss is None:
            violations.append("TIGHTEN_STOP without new_stop_loss")
            return _to_hold(decision), violations
        if decision.new_stop_loss < current_stop:
            violations.append(
                f"손절 하향 시도 거부: {current_stop} → {decision.new_stop_loss}"
            )
            return _to_hold(decision), violations
        return decision, violations

    if decision.action == "RAISE_TP":
        if decision.new_take_profit is None:
            violations.append("RAISE_TP without new_take_profit")
            return _to_hold(decision), violations
        if decision.new_take_profit <= current_tp:
            violations.append(
                f"익절 상향 아님: {current_tp} → {decision.new_take_profit}"
            )
            return _to_hold(decision), violations
        if decision.new_take_profit <= current_price:
            violations.append(
                f"익절을 현재가 이하로: 현재 {current_price} vs 신규 TP {decision.new_take_profit}"
            )
            return _to_hold(decision), violations
        # entry × (1 + max_take_profit_pct/100) absolute cap
        abs_cap = int(entry_price * (1 + rules.max_take_profit_pct / 100))
        new_tp = min(decision.new_take_profit, abs_cap)
        if new_tp != decision.new_take_profit:
            violations.append(
                f"익절 절대 cap 적용: {decision.new_take_profit} → {new_tp}"
            )
        return decision.model_copy(update={"new_take_profit": new_tp}), violations

    if decision.action == "CLOSE_NOW":
        if decision.confidence < rules.close_now_min_confidence:
            violations.append(
                f"CLOSE_NOW confidence 부족: {decision.confidence} < {rules.close_now_min_confidence}"
            )
            return _to_hold(decision), violations
        return decision, violations

    return decision, violations


def _to_hold(d: MonitorDecision) -> MonitorDecision:
    return d.model_copy(update={
        "action": "HOLD",
        "new_stop_loss": None,
        "new_take_profit": None,
        "close_urgency": None,
    })


def validate_market_regime(
    macro_indices: list[dict], rules: TradingRules
) -> tuple[bool, str | None]:
    """
    상향장 추격 게이트.
    BUY 후보 평가 전 호출. 약세장이면 (False, reason) 반환 → 전종목 BUY 차단.

    하향장 끝나는 시점은 별도로 잡지 않음.
    상향 전환 시 w_change_pct가 양수로 돌아오면 자동 통과.
    """
    def by_label(label: str) -> dict | None:
        return next((m for m in macro_indices if m.get("label") == label), None)

    kospi = by_label("코스피")
    kosdaq = by_label("코스닥")
    vix = by_label("VIX(공포지수)")

    if kospi:
        w = kospi.get("w_change_pct", 0.0)
        if w < rules.block_if_kospi_5d_pct_below:
            return False, f"KOSPI 5일 {w:+.2f}% < {rules.block_if_kospi_5d_pct_below:+.1f}% (약세장)"
        d = kospi.get("d_change_pct", 0.0)
        if d < rules.block_if_kospi_1d_pct_below:
            return False, f"KOSPI 당일 {d:+.2f}% < {rules.block_if_kospi_1d_pct_below:+.1f}% (panic day)"

    if kosdaq:
        w = kosdaq.get("w_change_pct", 0.0)
        if w < rules.block_if_kosdaq_5d_pct_below:
            return False, f"KOSDAQ 5일 {w:+.2f}% < {rules.block_if_kosdaq_5d_pct_below:+.1f}% (약세장)"

    if vix:
        v = vix.get("close", 0.0)
        if v > rules.block_if_vix_above:
            return False, f"VIX {v:.1f} > {rules.block_if_vix_above:.1f} (위험 회피)"

    return True, None
