"""stock-signal-bot 산출 signal JSON 로더 + JSONSchema 검증."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import jsonschema

log = logging.getLogger(__name__)


def load_signal(
    signal_dir: Path | str,
    target_date: date | None = None,
    schema_path: Path | str | None = None,
    max_age_min: int = 60,
    name_suffix: str = "",
) -> dict | None:
    """
    오늘 날짜(또는 target_date)의 signal JSON 로드.
    - 파일 없음 → None
    - 손상/스키마 위반 → None + log warning
    - generated_at이 max_age_min 초과 → None (stale)
    """
    target = target_date or date.today()
    p = Path(signal_dir).expanduser() / f"{target:%Y-%m-%d}{name_suffix}.json"

    if not p.exists():
        log.info("signal JSON 없음: %s", p)
        return None

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("signal JSON 손상 (%s): %s", p, e)
        return None

    if schema_path is not None:
        try:
            schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as e:
            log.warning("signal JSON 스키마 위반: %s", e.message)
            return None
        except (OSError, json.JSONDecodeError) as e:
            log.warning("스키마 파일 로드 실패: %s", e)

    # 신선도 체크
    try:
        generated_at = datetime.fromisoformat(data["generated_at"])
        age = datetime.now(tz=generated_at.tzinfo) - generated_at
        if age > timedelta(minutes=max_age_min):
            log.warning("signal JSON stale: age %s > %dmin", age, max_age_min)
            return None
    except (KeyError, ValueError) as e:
        log.warning("generated_at 파싱 실패: %s", e)

    return data


def latest_signal_date(signal_dir: Path | str, name_suffix: str = "") -> date | None:
    """signal_dir에서 가장 최근 날짜의 signal 파일 날짜 (carry-over용). 없으면 None."""
    import re
    pat = re.compile(r"^(\d{4}-\d{2}-\d{2})" + re.escape(name_suffix) + r"\.json$")
    dates: list[date] = []
    for f in Path(signal_dir).expanduser().glob("*.json"):
        m = pat.match(f.name)
        if m:
            try:
                dates.append(date.fromisoformat(m.group(1)))
            except ValueError:
                pass
    return max(dates) if dates else None


def filter_buy_candidates(signals: dict, min_score: int = 8) -> list[dict]:
    """진입 후보 필터.

    v2 JSON의 strategy_signals가 있으면 다중 전략 후보를 우선 사용하고,
    없으면 legacy buys를 사용한다.
    """
    strategy_signals = signals.get("strategy_signals") or []
    if strategy_signals:
        legacy_by_ticker = {b.get("ticker"): b for b in signals.get("buys", [])}
        out: list[dict] = []
        for s in strategy_signals:
            score = int(s.get("strategy_score", 0) or 0)
            if score < min_score or s.get("eligible") is False:
                continue
            c = dict(s)
            legacy = legacy_by_ticker.get(c.get("ticker"), {})
            c["score"] = score
            c.setdefault("triggers", s.get("triggers", []))
            c.setdefault("panel_summary", legacy.get("panel_summary", {}))
            c.setdefault("short_balance", s.get("short_balance") or legacy.get("short_balance"))
            c.setdefault("llm_analysis", legacy.get("llm_analysis"))
            out.append(c)
        return out

    return [b for b in signals.get("buys", []) if b.get("score", 0) >= min_score]
