"""Self-consistency — 진입 결정을 N회 호출 후 다수결 + 보수적 합산."""
from __future__ import annotations

import logging
from statistics import median
from typing import Callable

from src.llm.cli_client import call_llm
from src.llm.json_parser import parse_decision
from src.llm.schemas import EntryDecision

log = logging.getLogger(__name__)


def vote_entry(
    prompt: str,
    n: int = 3,
    timeout: int = 180,
    llm_fn: Callable[[str, int], tuple[str, str, int]] = call_llm,
) -> tuple[EntryDecision, list[dict]]:
    """
    N회 호출 → 다수결.
    Returns (final_decision, trace).
    보수성: 손절은 가장 타이트, 익절은 가장 얕은 값 선택.
    """
    trace: list[dict] = []

    decisions: list[EntryDecision] = []
    for i in range(n):
        text, source, elapsed_ms = llm_fn(prompt, timeout)
        parsed, err = parse_decision(text, EntryDecision)
        # raw 응답 첫 600자 stderr 출력 (디버그)
        log.warning("=== LLM iter %d/%d (src=%s, %dms) ===\n%s\n---end---",
                    i + 1, n, source, elapsed_ms, (text or "")[:600])
        if err:
            log.warning("parse_error: %s", err)
        trace.append({
            "iter": i + 1,
            "source": source,
            "elapsed_ms": elapsed_ms,
            "parse_error": err,
            "action": parsed.action if parsed else "INVALID",
            "confidence": parsed.confidence if parsed else 0,
            "raw_first_300": (text or "")[:300],
            "raw_text": (text or "")[:5000],
        })
        if parsed is not None:
            decisions.append(parsed)

    if not decisions:
        log.warning("self-consistency: %d회 호출 모두 파싱 실패", n)
        return EntryDecision(action="SKIP"), trace

    actions = [d.action for d in decisions]
    if actions.count("BUY") < (n + 1) // 2:
        log.info("self-consistency: BUY 다수결 미달 (BUY=%d/%d) → SKIP",
                 actions.count("BUY"), n)
        return EntryDecision(action="SKIP"), trace

    buys = [d for d in decisions if d.action == "BUY"]

    # 보수적 합산
    final = EntryDecision(
        action="BUY",
        entry_strategy=_mode([d.entry_strategy for d in buys]),
        entry_price=int(median([d.entry_price for d in buys])),
        size_pct=float(median([d.size_pct for d in buys])),
        stop_loss_pct=min(d.stop_loss_pct for d in buys),   # 가장 타이트한 손절폭
        take_profit_pct=min(d.take_profit_pct for d in buys),  # 가장 얕은
        max_hold_days=int(median([d.max_hold_days for d in buys])),
        confidence=int(median([d.confidence for d in buys])),
        key_thesis=buys[0].key_thesis,
        key_risks=list({r for d in buys for r in d.key_risks})[:5],
        watch_signals=list({w for d in buys for w in d.watch_signals})[:5],
    )
    return final, trace


def _mode(items: list[str]) -> str:
    """가장 빈도 높은 원소. tie 시 첫 등장 우선."""
    if not items:
        return ""
    counts: dict[str, int] = {}
    for x in items:
        counts[x] = counts.get(x, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]
