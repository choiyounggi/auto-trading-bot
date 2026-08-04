"""LLM 응답 JSON 파싱 — 마크다운 fence 제거 + Pydantic 검증."""
from __future__ import annotations

import json
import logging
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# 마크다운 코드 fence (```json ... ```) 제거
_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?|\n?\s*```\s*$", re.MULTILINE)


def parse_decision(text: str, model_cls: type[T]) -> tuple[T | None, str | None]:
    """
    Returns (parsed, error). 둘 중 하나는 None.
    LLM이 마크다운 fence 우기는 케이스 대응.
    """
    if not text:
        return None, "empty"

    cleaned = _FENCE_RE.sub("", text.strip()).strip()
    if not cleaned:
        return None, "empty after fence strip"

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        # 첫 { 부터 마지막 } 사이만 추출 시도 (LLM이 앞뒤에 추가 텍스트 붙이는 경우)
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return None, f"json decode: {e}"
        else:
            return None, f"json decode: {e}"

    try:
        return model_cls.model_validate(data), None
    except ValidationError as e:
        return None, f"validation: {e}"
