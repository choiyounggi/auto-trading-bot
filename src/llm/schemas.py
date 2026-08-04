"""Pydantic 결정 스키마 — LLM 응답 검증."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EntryDecision(BaseModel):
    """진입 결정. orchestrator에서 self-consistency 3회 호출 후 다수결.

    action=SKIP 일 때 entry 필드는 null/0 허용 (LLM이 채울 의미 없음).
    confidence는 int 강제 시 LLM이 4.5 같은 소수점 출력 → ValidationError →
    BUY 안 되는 SKIP 결정조차 파싱 fail. → float 허용.
    """

    model_config = {"extra": "ignore"}  # 추가 필드 무시

    action: Literal["BUY", "SKIP"]

    # action=BUY 일 때만 의미 — SKIP이면 LLM이 null 또는 0 줄 수 있음. None 허용.
    entry_strategy: Literal["MARKET_OPEN", "LIMIT_TODAY_AFTER_HOURS"] | None = "MARKET_OPEN"
    entry_price: int | None = Field(default=0, ge=0, le=10_000_000)
    size_pct: float | None = Field(default=0.0, ge=0.0, le=30.0)   # clamp가 20%로 자름
    stop_loss_pct: float | None = Field(default=0.0, ge=0.0, le=10.0)
    take_profit_pct: float | None = Field(default=0.0, ge=0.0, le=20.0)
    max_hold_days: int | None = Field(default=5, ge=0, le=15)

    # 보조 — confidence int → float (LLM 4.5 출력 흔함)
    confidence: float = Field(default=0.0, ge=0.0, le=10.0)
    key_thesis: str = Field(default="", max_length=400)
    key_risks: list[str] = Field(default_factory=list, max_length=10)
    watch_signals: list[str] = Field(default_factory=list, max_length=10)


class MonitorDecision(BaseModel):
    """동적 조정 결정. 정규장 30분 주기."""

    action: Literal["HOLD", "TIGHTEN_STOP", "RAISE_TP", "CLOSE_NOW"]

    new_stop_loss: int | None = None
    new_take_profit: int | None = None
    close_urgency: Literal["END_OF_DAY", "IMMEDIATE"] | None = None

    confidence: int = Field(default=0, ge=0, le=10)
    reason: str = Field(default="", max_length=300)
