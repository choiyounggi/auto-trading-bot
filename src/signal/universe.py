"""KOSPI200 + KOSDAQ150 종목 코드 로드.

전략 (순서대로 시도):
  1) data/cache/universe_<INDEX>.txt 캐시 파일
  2) pykrx.stock.get_index_portfolio_deposit_file (KRX 정식 endpoint)
  3) pykrx.stock.get_market_cap_by_ticker (시가총액 상위 N개로 대체)

성공 시 캐시 파일에 저장 → 다음 실행은 즉시 로드.
"""
from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from pykrx import stock

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 인덱스 → (시가총액 폴백 시장, 폴백 상위 N개)
FALLBACK_RULES: dict[str, tuple[str, int]] = {
    "1028": ("KOSPI", 200),   # KOSPI200
    "2203": ("KOSDAQ", 150),  # KOSDAQ150
}


def _cache_file(index_code: str) -> Path:
    return CACHE_DIR / f"universe_{index_code}.txt"


def _read_cache(index_code: str) -> list[str] | None:
    p = _cache_file(index_code)
    if not p.exists():
        return None
    tickers = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return tickers or None


def _write_cache(index_code: str, tickers: Iterable[str]) -> None:
    p = _cache_file(index_code)
    p.write_text("\n".join(tickers) + "\n", encoding="utf-8")


def _fetch_via_index(index_code: str, ref_date: str) -> list[str] | None:
    try:
        tickers = stock.get_index_portfolio_deposit_file(index_code, date=ref_date)
        return list(tickers) if tickers else None
    except Exception as e:
        log.warning("index_portfolio_deposit_file(%s) 실패: %s", index_code, e)
        return None


def _fetch_via_marketcap(index_code: str, ref_date: str) -> list[str] | None:
    rule = FALLBACK_RULES.get(index_code)
    if not rule:
        return None
    market, top_n = rule
    try:
        df = stock.get_market_cap_by_ticker(ref_date, market=market)
        if df is None or df.empty:
            return None
        df_sorted = df.sort_values("시가총액", ascending=False)
        return list(df_sorted.head(top_n).index)
    except Exception as e:
        log.warning("get_market_cap_by_ticker(%s) 실패: %s", market, e)
        return None


@lru_cache(maxsize=4)
def get_index_constituents(index_code: str, ref_date: str | None = None) -> tuple[str, ...]:
    if ref_date is None:
        ref_date = date.today().strftime("%Y%m%d")

    cached = _read_cache(index_code)
    if cached:
        log.info("universe[%s] from cache (%d)", index_code, len(cached))
        return tuple(cached)

    tickers = _fetch_via_index(index_code, ref_date)
    src = "index_deposit_file"
    if not tickers:
        log.warning("universe[%s]: index endpoint 실패 → 시가총액 fallback", index_code)
        tickers = _fetch_via_marketcap(index_code, ref_date)
        src = "market_cap"
    if not tickers:
        log.error("universe[%s]: 모든 fetch 실패. 빈 리스트 반환.", index_code)
        return tuple()

    _write_cache(index_code, tickers)
    log.info("universe[%s] from %s, cached (%d)", index_code, src, len(tickers))
    return tuple(tickers)


def load_universe(indices: dict[str, str], ref_date: str | None = None) -> dict[str, str]:
    universe: dict[str, str] = {}
    for name, code in indices.items():
        for t in get_index_constituents(code, ref_date):
            universe.setdefault(t, name)
    return universe


def ticker_to_name(tickers: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for t in tickers:
        try:
            out[t] = stock.get_market_ticker_name(t)
        except Exception:
            out[t] = t
    return out
