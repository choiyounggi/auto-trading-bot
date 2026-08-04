"""LLM 동적 조정 — 보유 종목별 30분 주기 결정."""
from __future__ import annotations

import logging
from datetime import date

from src.guardrails.clamp import validate_monitor
from src.guardrails.rules import TradingRules
from src.llm.cli_client import call_llm
from src.llm.json_parser import parse_decision
from src.llm.prompts import MONITOR_PROMPT_TEMPLATE
from src.llm.schemas import MonitorDecision

log = logging.getLogger(__name__)


def build_prompt(position, current_price: int, today_high: int, today_low: int,
                 volume_ratio: float, macro_brief: str, recent_news: str,
                 rules: TradingRules) -> str:
    hold_days = (date.today() - position.entry_at.date()).days if position.entry_at else 0
    pnl_pct = (current_price / position.entry_price_actual - 1) * 100 if position.entry_price_actual else 0.0

    return MONITOR_PROMPT_TEMPLATE.format(
        ticker=position.ticker,
        name=position.name,
        entry_price=position.entry_price_actual or 0,
        entry_at=position.entry_at,
        current_stop=position.current_stop_loss or 0,
        current_tp=position.current_take_profit or 0,
        hold_days=hold_days,
        max_hold_days=position.max_hold_until,
        tp_raised_count=position.tp_raised_count,
        max_tp_raises=rules.max_tp_raises,
        trailing_active=bool(position.trailing_active),
        key_thesis=position.entry_thesis or "",
        watch_signals=position.watch_signals_json or "",
        current_price=current_price,
        pnl_pct=pnl_pct,
        volume_ratio=volume_ratio,
        today_high=today_high,
        today_low=today_low,
        macro_brief=macro_brief,
        recent_news=recent_news,
    )


def decide_monitor(position, current_price: int, today_high: int, today_low: int,
                   volume_ratio: float, macro_brief: str, recent_news: str,
                   rules: TradingRules) -> tuple[MonitorDecision, list[str], dict]:
    """LLM 호출 + 안전 규칙 검증.

    Returns (decision, violations, trace).
    trace: LLM 호출 메타 (source, elapsed_ms, parse_error)
    """
    prompt = build_prompt(position, current_price, today_high, today_low,
                          volume_ratio, macro_brief, recent_news, rules)

    text, source, elapsed_ms = call_llm(prompt, timeout=rules.timeout_sec_monitor)
    parsed, err = parse_decision(text, MonitorDecision)

    trace = {
        "source": source,
        "elapsed_ms": elapsed_ms,
        "parse_error": err,
        "raw_text": text[:500],
    }

    if parsed is None:
        # 파싱 실패 → 안전 기본값 HOLD
        log.warning("monitor LLM 파싱 실패 (%s): %s", position.ticker, err)
        return MonitorDecision(action="HOLD", confidence=0, reason="LLM 파싱 실패"), [], trace

    final, violations = validate_monitor(
        parsed,
        current_stop=position.current_stop_loss or 0,
        current_tp=position.current_take_profit or 0,
        current_price=current_price,
        entry_price=position.entry_price_actual or 0,
        rules=rules,
    )

    return final, violations, trace
