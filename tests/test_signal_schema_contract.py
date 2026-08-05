"""생산자(dump_signals_json) ↔ 계약(schemas/signal-v1.json) ↔ 소비자(load_signal) 왕복 검증.

신호 봇과 트레이더가 한 패키지에 있게 된 뒤로 둘 사이의 유일한 계약은 이 JSON 파일이다.
생산자가 필드를 바꾸고 소비자가 모르면 매매가 조용히 멈춘다 — 이 파일이 그걸 막는다.

검증은 소비자와 **똑같은 방식**으로 한다: `jsonschema.validate(data, schema)` (FormatChecker
없음). 여기서만 더 엄격하게 검사하면 실제로는 트레이더가 받아들이는 파일을 테스트가 거절해
계약을 잘못 보고하게 된다.

네트워크(KRX/yfinance/Brave/Telegram)는 건드리지 않는다 — 페이로드를 직접 구성한다.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import jsonschema
import pandas as pd
import pytest

from src.orchestrator.signal_loader import load_signal
from src.signal.analysis.signal_engine import TickerSignal
from src.signal.data.dump_signals import dump_signals_json
from src.signal.data.macro_context import IndexSnapshot
from src.signal.data.news_brave import NewsItem

TODAY = date(2026, 8, 5)
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "signal-v1.json"

# 신선도가 스키마 검사를 가리지 않도록 넉넉하게 (generated_at 은 dump 시점 = now).
GENEROUS_MAX_AGE_MIN = 60 * 24 * 365


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _panel() -> pd.DataFrame:
    """summarize_panel 이 읽는 컬럼만 담은 최소 패널 (5거래일)."""
    return pd.DataFrame(
        {
            "종가": [70000, 70500, 71000, 70800, 72000],
            "등락률": [0.4, 0.71, 0.71, -0.28, 1.69],
            "거래량": [10_000_000] * 4 + [18_000_000],
            "foreign_net": [1_000, 2_000, -500, 3_000, 4_000],
            "inst_net": [500, -200, 1_500, 800, 2_200],
        }
    )


def _realistic_kwargs() -> dict:
    """생산 코드가 실제로 넘기는 모양의 인자 — 데이터클래스 + 패널 + LLM 결과."""
    return {
        "today": TODAY,
        "buys": [
            TickerSignal(
                ticker="005930",
                name="삼성전자",
                score=11,
                triggers=["A1:기관+외국인 동시매수", "B2:20일 신고가"],
            )
        ],
        "cautions": [
            TickerSignal(
                ticker="000660",
                name="SK하이닉스",
                score=-6,
                triggers=["C1:외국인 연속 순매도"],
            )
        ],
        "panels": {"005930": _panel()},
        "llm_results": {
            "005930": {"source": "claude", "text": "수급 개선 지속", "length": 8}
        },
        "short_balances": {
            "005930": {
                "ticker": "005930",
                "latest_pct": 1.23,
                "pct_5d_change": -0.11,
                "pct_20d_change": 0.42,
                "latest_date": "2026-08-01",
                "days_lag": 2,
            }
        },
        "macro_snaps": [
            IndexSnapshot(
                label="KOSPI",
                symbol="^KS11",
                close=2712.34,
                d_change_pct=0.62,
                w_change_pct=1.85,
                rows=10,
            )
        ],
        "macro_news": [
            NewsItem(
                title="Fed holds rates steady",
                url="https://example.com/fed",
                description="정책금리 동결",
                age="1 day ago",
            )
        ],
        "naver_data": {
            "kospi_quote": {"close": 2712.34, "change_pct": 0.62},
            "kosdaq_quote": None,
            "kospi_flow": {"foreign": 1200, "inst": -300},
            "kosdaq_flow": None,
            "headlines": [{"title": "코스피 강세"}],
        },
        "strategy_signals": [
            {
                "ticker": "005930",
                "name": "삼성전자",
                "strategy_id": "breakout_v2",
                "strategy_version": "2.1",
                "strategy_score": 12,
                "triggers": ["돈치안 20일 상단 돌파"],
                "features": {"atr20_pct": 2.1},
                "eligible": True,
                "filter_reasons": [],
            }
        ],
        "fundamentals": {"005930": {"per": 13.2, "pbr": 1.1}},
    }


def _empty_kwargs() -> dict:
    """신호가 하나도 없는 날 — 정상 상황이지 에러가 아니다."""
    return {
        "today": TODAY,
        "buys": [],
        "cautions": [],
        "panels": {},
        "llm_results": {},
        "short_balances": {},
        "macro_snaps": [],
        "macro_news": [],
        "strategy_signals": [],
        "fundamentals": {},
    }


def test_schema_file_exists_where_both_sides_look_for_it():
    """SCHEMA_PATH 가 틀리면 load_signal 은 OSError 를 삼키고 **검증 없이 통과시킨다**.

    그러면 이 파일의 왕복 테스트 전체가 스키마를 한 번도 안 보고 초록이 된다.
    경로부터 못 박아 그 무의미한 초록을 차단한다.
    """
    assert SCHEMA_PATH.is_file(), f"트레이더의 계약 파일이 없다: {SCHEMA_PATH}"
    assert _schema()["properties"]["schema_version"]["const"] == "1.0"


# --- 정상 ---------------------------------------------------------------


def test_produced_file_validates_against_the_traders_schema(tmp_path):
    """생산자가 쓴 파일이 트레이더의 계약(schemas/signal-v1.json)을 통과한다."""
    out_path = dump_signals_json(out_dir=tmp_path, **_realistic_kwargs())

    data = json.loads(out_path.read_text(encoding="utf-8"))
    jsonschema.validate(data, _schema())  # 위반 시 ValidationError 로 실패

    # 스키마가 "아무거나 통과"시키는 게 아니라 실제 내용이 실렸는지 확인.
    assert data["schema_version"] == "1.0"
    assert data["date"] == "2026-08-05"
    assert [b["ticker"] for b in data["buys"]] == ["005930"]
    assert [c["ticker"] for c in data["cautions"]] == ["000660"]
    assert data["macro"]["indices"][0]["symbol"] == "^KS11"
    assert data["buys"][0]["panel_summary"]["last_close"] == 72000


def test_produced_file_round_trips_through_the_real_consumer(tmp_path):
    """재구현이 아닌 **트레이더의 load_signal** 이 같은 디렉토리를 읽어 dict 를 돌려준다.

    load_signal 에 schema_path 를 넘겨 소비자 쪽에서도 스키마 검증이 실제로 돌게 한다.
    """
    dump_signals_json(out_dir=tmp_path, **_realistic_kwargs())

    loaded = load_signal(
        tmp_path,
        target_date=TODAY,
        schema_path=SCHEMA_PATH,
        max_age_min=GENEROUS_MAX_AGE_MIN,
    )

    assert loaded is not None, "소비자가 자기 생산자의 파일을 거절했다"
    assert isinstance(loaded, dict)
    assert isinstance(loaded["strategy_signals"], list)
    assert loaded["strategy_signals"][0]["strategy_id"] == "breakout_v2"
    assert loaded["strategy_signals"][0]["strategy_score"] == 12
    assert loaded["date"] == "2026-08-05"
    assert loaded["buys"][0]["name"] == "삼성전자"


def test_strategy_signals_survive_into_the_traders_candidate_filter(tmp_path):
    """왕복한 dict 가 트레이더의 실제 소비 지점(filter_buy_candidates)에서도 쓰인다."""
    from src.orchestrator.signal_loader import filter_buy_candidates

    dump_signals_json(out_dir=tmp_path, **_realistic_kwargs())
    loaded = load_signal(
        tmp_path,
        target_date=TODAY,
        schema_path=SCHEMA_PATH,
        max_age_min=GENEROUS_MAX_AGE_MIN,
    )

    candidates = filter_buy_candidates(loaded, min_score=8)

    assert [c["ticker"] for c in candidates] == ["005930"]
    assert candidates[0]["score"] == 12


# --- 에러 (음성 대조군) --------------------------------------------------


def test_removing_a_required_key_is_rejected(tmp_path):
    """**음성 대조군**: 이 검사가 공허하지 않다는 증거.

    스키마가 사실상 아무거나 허용한다면 위 테스트들은 계약이 깨져도 초록으로 남는다.
    필수 키를 제거한 파일이 확실히 ValidationError 를 내는지 — 그리고 소비자가 그
    파일을 거절하는지 — 확인해야 위 초록이 의미를 갖는다.
    """
    out_path = dump_signals_json(out_dir=tmp_path, **_realistic_kwargs())

    data = json.loads(out_path.read_text(encoding="utf-8"))
    del data["buys"]
    out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(jsonschema.ValidationError) as exc:
        jsonschema.validate(data, _schema())

    assert exc.value.validator == "required"
    assert "buys" in str(exc.value), str(exc.value)

    # 소비자 쪽 대조군: 트레이더는 계약 위반 파일을 읽어들이지 않는다.
    assert (
        load_signal(
            tmp_path,
            target_date=TODAY,
            schema_path=SCHEMA_PATH,
            max_age_min=GENEROUS_MAX_AGE_MIN,
        )
        is None
    )


def test_strategy_signals_must_be_an_array(tmp_path):
    """**음성 대조군**: 타입이 어긋나도 통과한다면 계약은 아무것도 보장하지 못한다.

    strategy_signals 는 트레이더가 순회하는 리스트다 — 문자열이 들어오면 글자 단위로
    도는 조용한 오작동이 되므로 스키마가 여기서 막아야 한다.
    """
    out_path = dump_signals_json(out_dir=tmp_path, **_realistic_kwargs())

    data = json.loads(out_path.read_text(encoding="utf-8"))
    data["strategy_signals"] = "breakout_v2"
    out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(jsonschema.ValidationError) as exc:
        jsonschema.validate(data, _schema())

    assert exc.value.validator == "type"
    assert list(exc.value.absolute_path) == ["strategy_signals"]
    assert "array" in exc.value.message

    assert (
        load_signal(
            tmp_path,
            target_date=TODAY,
            schema_path=SCHEMA_PATH,
            max_age_min=GENEROUS_MAX_AGE_MIN,
        )
        is None
    )


def test_strategy_signal_missing_its_own_required_key_is_rejected(tmp_path):
    """중첩된 정의($ref strategySignal)까지 실제로 걸리는지 — 최상위만 검사되면 반쪽이다."""
    out_path = dump_signals_json(out_dir=tmp_path, **_realistic_kwargs())

    data = json.loads(out_path.read_text(encoding="utf-8"))
    del data["strategy_signals"][0]["strategy_id"]
    out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(jsonschema.ValidationError) as exc:
        jsonschema.validate(data, _schema())

    assert exc.value.validator == "required"
    assert "strategy_id" in str(exc.value)
    assert list(exc.value.absolute_path) == ["strategy_signals", 0]


# --- 경계값 -------------------------------------------------------------


def test_empty_signal_day_validates_and_still_round_trips(tmp_path):
    """신호 0건인 날도 정상이다 — 계약 위반이 아니라 '오늘은 없음'이어야 한다."""
    out_path = dump_signals_json(out_dir=tmp_path, **_empty_kwargs())

    data = json.loads(out_path.read_text(encoding="utf-8"))
    jsonschema.validate(data, _schema())

    assert data["buys"] == []
    assert data["cautions"] == []
    assert data["strategy_signals"] == []
    assert data["macro"]["indices"] == []

    loaded = load_signal(
        tmp_path,
        target_date=TODAY,
        schema_path=SCHEMA_PATH,
        max_age_min=GENEROUS_MAX_AGE_MIN,
    )

    assert loaded is not None, "신호 없는 날을 소비자가 에러로 취급하면 안 된다"
    assert loaded["buys"] == []
    assert loaded["strategy_signals"] == []


def test_suffixed_file_also_validates_and_round_trips(tmp_path):
    """해외장(.us) 처럼 name_suffix 가 붙어도 같은 계약을 지킨다 — 파일명만 다르다."""
    out_path = dump_signals_json(
        out_dir=tmp_path, name_suffix=".us", **_realistic_kwargs()
    )

    assert out_path.name == "2026-08-05.us.json"
    jsonschema.validate(json.loads(out_path.read_text(encoding="utf-8")), _schema())

    loaded = load_signal(
        tmp_path,
        target_date=TODAY,
        schema_path=SCHEMA_PATH,
        max_age_min=GENEROUS_MAX_AGE_MIN,
        name_suffix=".us",
    )

    assert loaded is not None
    assert loaded["strategy_signals"][0]["ticker"] == "005930"
