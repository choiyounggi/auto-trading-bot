"""stock-signal-bot에 추가할 signal JSON dump 모듈.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.orchestrator.signal_loader import resolve_signal_dir

log = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"


def _asdict_safe(obj):
    if obj is None:
        return None
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return obj


def _serialize_naver(data: dict) -> dict:
    """naver_finance.fetch_all_kospi_kosdaq 결과 → JSON 직렬화 가능 dict."""
    if not data:
        return {}
    return {
        "kospi_quote": _asdict_safe(data.get("kospi_quote")),
        "kosdaq_quote": _asdict_safe(data.get("kosdaq_quote")),
        "kospi_flow": _asdict_safe(data.get("kospi_flow")),
        "kosdaq_flow": _asdict_safe(data.get("kosdaq_flow")),
        "headlines": [_asdict_safe(h) for h in (data.get("headlines") or [])],
    }


def summarize_panel(panel: pd.DataFrame) -> dict:
    if panel is None or panel.empty:
        return {}
    last = panel.iloc[-1]
    last5 = panel.tail(5)
    avg_vol_20d = panel["거래량"].tail(20).mean() if "거래량" in panel.columns else 0
    last_vol = float(last.get("거래량", 0))
    foreign5 = int(last5["foreign_net"].sum()) if "foreign_net" in panel.columns else 0
    inst5 = int(last5["inst_net"].sum()) if "inst_net" in panel.columns else 0
    return {
        "last_close": int(last.get("종가", 0)),
        "d_change_pct": float(last.get("등락률", 0.0)),
        "vol_ratio_5d": float(last_vol / avg_vol_20d) if avg_vol_20d > 0 else 0.0,
        "foreign_net_5d_won": foreign5,
        "inst_net_5d_won": inst5,
    }


def dump_signals_json(
    today: date,
    buys: list,
    cautions: list,
    panels: dict,
    llm_results: dict,
    short_balances: dict,
    macro_snaps: list,
    macro_news: list,
    naver_data: dict | None = None,
    strategy_signals: list[dict] | None = None,
    fundamentals: dict | None = None,
    out_dir: Path | str | None = None,
    name_suffix: str = "",
) -> Path:
    """오늘 시그널 + LLM 분석 + 매크로를 JSON으로 저장.

    stock-trader의 orchestrator가 이 JSON을 읽어 진입 결정.
    """
    out_dir = Path(out_dir) if out_dir else resolve_signal_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{today:%Y-%m-%d}{name_suffix}.json"

    out = {
        "schema_version": SCHEMA_VERSION,
        "date": today.isoformat(),
        "generated_at": datetime.now().astimezone().isoformat(),
        "buys": [],
        "cautions": [],
        "strategy_signals": strategy_signals or [],
        "fundamentals": fundamentals or {},
        "macro": {
            "indices": [_asdict_safe(s) for s in macro_snaps],
            "headlines": [_asdict_safe(n) for n in (macro_news or [])][:10],
        },
        "naver": _serialize_naver(naver_data or {}),
    }

    for sig in buys:
        ticker = sig.ticker
        panel = panels.get(ticker, pd.DataFrame()) if panels else pd.DataFrame()
        sb = short_balances.get(ticker) if short_balances else None
        out["buys"].append({
            "ticker": ticker,
            "name": sig.name,
            "score": int(sig.score),
            "triggers": list(sig.triggers),
            "panel_summary": summarize_panel(panel),
            "short_balance": _asdict_safe(sb),
            "llm_analysis": llm_results.get(ticker) if llm_results else None,
        })

    for sig in cautions:
        out["cautions"].append({
            "ticker": sig.ticker,
            "name": sig.name,
            "score": int(sig.score),
            "triggers": list(sig.triggers),
        })

    # 같은 디렉토리의 임시 파일에 쓴 뒤 os.replace — 트레이더가 반쯤 쓰인 파일을
    # 읽는 일이 없어야 한다. 크로스 파일시스템 rename 은 원자적이지 않으므로
    # 임시 파일은 반드시 목적지와 같은 디렉토리에 만든다.
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        tmp_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, out_path)
    finally:
        # 실패한 실행이 .tmp 를 남기면 트레이더가 훑는 디렉토리를 어지럽힌다.
        tmp_path.unlink(missing_ok=True)
    log.info(
        "signal JSON 저장: %s (buys=%d, cautions=%d, strategy_signals=%d)",
        out_path, len(out["buys"]), len(out["cautions"]), len(out["strategy_signals"])
    )
    return out_path
