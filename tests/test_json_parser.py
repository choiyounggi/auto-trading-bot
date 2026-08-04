"""LLM 응답 JSON 파싱 테스트."""
from __future__ import annotations

from src.llm.json_parser import parse_decision
from src.llm.schemas import EntryDecision, MonitorDecision


def _entry_json() -> str:
    return '{"action":"BUY","entry_price":142500,"size_pct":3.0,"stop_loss_pct":2.0,"take_profit_pct":3.0,"max_hold_days":5,"confidence":9,"key_thesis":"외인 매수 가속"}'


# 정상: 깨끗한 JSON
def test_parse_clean_json():
    parsed, err = parse_decision(_entry_json(), EntryDecision)
    assert err is None
    assert parsed is not None
    assert parsed.action == "BUY"
    assert parsed.entry_price == 142500


# 정상: 마크다운 fence 자동 제거 (```json ... ```)
def test_parse_markdown_fence():
    raw = f"```json\n{_entry_json()}\n```"
    parsed, err = parse_decision(raw, EntryDecision)
    assert err is None
    assert parsed is not None


# 정상: 코드 fence 없는 ``` 만 있는 경우
def test_parse_bare_fence():
    raw = f"```\n{_entry_json()}\n```"
    parsed, err = parse_decision(raw, EntryDecision)
    assert err is None
    assert parsed is not None


# 경계값: LLM이 앞뒤에 설명 텍스트 붙임
def test_parse_with_surrounding_text():
    raw = f"분석 결과는 아래와 같습니다.\n{_entry_json()}\n끝."
    parsed, err = parse_decision(raw, EntryDecision)
    assert err is None
    assert parsed is not None
    assert parsed.action == "BUY"


# 에러: 빈 문자열
def test_parse_empty():
    parsed, err = parse_decision("", EntryDecision)
    assert parsed is None
    assert err == "empty"


# 에러: 깨진 JSON
def test_parse_invalid_json():
    parsed, err = parse_decision("not json at all", EntryDecision)
    assert parsed is None
    assert err is not None


# 에러: Pydantic validation 실패 (action 없음)
def test_parse_validation_error():
    raw = '{"entry_price":100,"size_pct":3.0}'
    parsed, err = parse_decision(raw, EntryDecision)
    assert parsed is None
    assert err is not None
    assert "validation" in err


# 정상: MonitorDecision도 같은 parser 동작
def test_parse_monitor_decision():
    raw = '{"action":"HOLD","confidence":7,"reason":"안정"}'
    parsed, err = parse_decision(raw, MonitorDecision)
    assert err is None
    assert parsed is not None
    assert parsed.action == "HOLD"
