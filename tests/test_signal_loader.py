"""signal_loader 다중 전략 후보 필터 테스트."""
from __future__ import annotations

from src.orchestrator.signal_loader import filter_buy_candidates


def test_strategy_signals_preferred_over_legacy_buys():
    signals = {
        "buys": [{"ticker": "005930", "name": "삼성전자", "score": 5, "triggers": ["legacy"]}],
        "strategy_signals": [
            {
                "ticker": "000660",
                "name": "SK하이닉스",
                "strategy_id": "price_momentum",
                "strategy_score": 6,
                "eligible": True,
                "triggers": ["PM"],
                "features": {"return_20d_pct": 8.0},
            }
        ],
    }

    out = filter_buy_candidates(signals, min_score=5)

    assert len(out) == 1
    assert out[0]["ticker"] == "000660"
    assert out[0]["score"] == 6
    assert out[0]["strategy_id"] == "price_momentum"


def test_strategy_signals_filter_low_score_and_ineligible():
    signals = {
        "strategy_signals": [
            {"ticker": "005930", "name": "삼성전자", "strategy_id": "flow_momentum", "strategy_score": 4, "eligible": True, "triggers": []},
            {"ticker": "000660", "name": "SK하이닉스", "strategy_id": "price_momentum", "strategy_score": 6, "eligible": False, "triggers": []},
        ]
    }

    assert filter_buy_candidates(signals, min_score=5) == []


def test_legacy_buys_fallback():
    signals = {
        "buys": [
            {"ticker": "005930", "name": "삼성전자", "score": 4, "triggers": []},
            {"ticker": "000660", "name": "SK하이닉스", "score": 5, "triggers": []},
        ]
    }

    out = filter_buy_candidates(signals, min_score=5)

    assert len(out) == 1
    assert out[0]["ticker"] == "000660"


# ── name_suffix 파일 라우팅 (클로버 분리, 2026-06-29 추가) ──────────────
import json
from datetime import date, datetime

from src.orchestrator.signal_loader import load_signal


def _write_signal(d, name):
    p = d / name
    p.write_text(json.dumps({
        "schema_version": "1.0",
        "date": date.today().isoformat(),
        "generated_at": datetime.now().astimezone().isoformat(),
        "buys": [],
        "strategy_signals": [],
    }), encoding="utf-8")
    return p


def test_load_signal_us_suffix_reads_separate_file(tmp_path):
    _write_signal(tmp_path, f"{date.today():%Y-%m-%d}.us.json")
    assert load_signal(tmp_path, name_suffix=".us") is not None   # .us 파일 읽음
    assert load_signal(tmp_path) is None                          # 평문 {date}.json 없음 → None


def test_load_signal_default_reads_plain_file(tmp_path):
    _write_signal(tmp_path, f"{date.today():%Y-%m-%d}.json")
    assert load_signal(tmp_path) is not None                      # 평문 읽음
    assert load_signal(tmp_path, name_suffix=".us") is None       # .us 없음 → None (국내가 .us 안 읽음)


def test_latest_signal_date(tmp_path):
    from src.orchestrator.signal_loader import latest_signal_date
    import datetime as _dt
    for n in ("2026-06-27.json", "2026-06-30.json", "2026-06-29.us.json"):
        (tmp_path / n).write_text("{}", encoding="utf-8")
    assert latest_signal_date(tmp_path) == _dt.date(2026, 6, 30)        # 국장 최신, .us 제외
    assert latest_signal_date(tmp_path, ".us") == _dt.date(2026, 6, 29)  # 미장 최신
    assert latest_signal_date(tmp_path / "none") is None                 # 디렉토리 없음 → None
