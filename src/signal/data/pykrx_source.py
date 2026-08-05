"""pykrx 일봉 OHLCV / 투자자별 순매수 수집 (per-ticker, 파일 캐시)."""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from pykrx import stock

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# trading_value_by_date(detail=True) 컬럼 매핑.
# pykrx 1.x 버전별 컬럼 차이를 흡수: "외국인합계"(구) 또는 "외국인"+"기타외국인"(신) 등.
INSTITUTION_COMPONENTS = ("금융투자", "보험", "투신", "사모", "은행", "기타금융", "연기금", "연기금등")
FOREIGN_COMPONENTS = ("외국인", "기타외국인", "외국인합계")  # 신/구 호환


def _series_or_zero(df, col):
    return df[col] if col in df.columns else 0


def _cache_path(kind: str, ticker: str, start: str, end: str) -> Path:
    return CACHE_DIR / f"{kind}_{ticker}_{start}_{end}.parquet"


def _read_cache(p: Path) -> pd.DataFrame | None:
    if p.exists():
        try:
            return pd.read_parquet(p)
        except Exception as e:
            log.warning("cache read fail %s: %s", p, e)
    return None


def _write_cache(p: Path, df: pd.DataFrame) -> None:
    try:
        df.to_parquet(p)
    except Exception as e:
        log.warning("cache write fail %s: %s", p, e)


def trading_days(end: date, n: int) -> list[str]:
    """end 이전 n 거래일(end 포함). 삼성전자(005930) OHLCV로 영업일 추출."""
    start = end - timedelta(days=n * 2 + 10)
    df = stock.get_market_ohlcv_by_date(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "005930"
    )
    if df.empty:
        raise RuntimeError(
            f"trading_days: pykrx 응답 비어있음 ({start}~{end}). KRX 사이트 점검 가능성."
        )
    days = [d.strftime("%Y%m%d") for d in df.index]
    return days[-n:]


def _retry(fn, *args, retries: int = 3, sleep: float = 1.0, **kwargs):
    last = None
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last = e
            log.warning("pykrx call failed (attempt %d/%d): %s", attempt + 1, retries, e)
            time.sleep(sleep * (attempt + 1))
    raise RuntimeError(f"pykrx retries exhausted: {last}")


def fetch_ticker_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    """종목별 OHLCV 시계열 (캐시). index: datetime, columns: 시가/고가/저가/종가/거래량/거래대금/등락률"""
    cache = _cache_path("ohlcv", ticker, start, end)
    cached = _read_cache(cache)
    if cached is not None:
        return cached
    df = _retry(stock.get_market_ohlcv_by_date, start, end, ticker)
    if not df.empty:
        _write_cache(cache, df)
    time.sleep(0.15)
    return df


def fetch_ticker_trading_value(ticker: str, start: str, end: str) -> pd.DataFrame:
    """종목별 투자자별 순매수 거래대금 시계열 (detail=True 포함, 캐시).

    columns 일부: 금융투자, 보험, 투신, 사모, 은행, 기타금융, 연기금등, 기관합계,
                  기타법인, 개인, 외국인, 기타외국인, 외국인합계, 전체
    """
    cache = _cache_path("flow", ticker, start, end)
    cached = _read_cache(cache)
    if cached is not None:
        return cached
    df = _retry(
        stock.get_market_trading_value_by_date,
        start, end, ticker, etf=False, etn=False, elw=False, detail=True,
    )
    if not df.empty:
        _write_cache(cache, df)
    time.sleep(0.15)
    return df


def fetch_ticker_panel(ticker: str, days: list[str]) -> pd.DataFrame:
    """한 종목의 시계열 패널.
    columns: 종가, 거래량, 등락률, foreign_net, inst_net, indiv_net, finance_net
    index: 'YYYYMMDD' 문자열
    """
    start, end = days[0], days[-1]
    ohlcv = fetch_ticker_ohlcv(ticker, start, end)
    flow = fetch_ticker_trading_value(ticker, start, end)

    if ohlcv.empty:
        return pd.DataFrame()

    # ohlcv index: pandas Timestamp → str
    ohlcv = ohlcv.copy()
    ohlcv.index = [d.strftime("%Y%m%d") for d in ohlcv.index]
    price_cols = [c for c in ("시가", "고가", "저가", "종가", "거래량", "등락률") if c in ohlcv.columns]
    panel = ohlcv[price_cols].copy()

    if not flow.empty:
        flow = flow.copy()
        flow.index = [d.strftime("%Y%m%d") for d in flow.index]

        # 외국인합계 = 외국인 + 기타외국인 (신 버전) 또는 "외국인합계"(구 버전)
        if "외국인합계" in flow.columns:
            panel["foreign_net"] = flow["외국인합계"]
        else:
            panel["foreign_net"] = sum(
                _series_or_zero(flow, c) for c in ("외국인", "기타외국인")
            )

        # 기관합계 = 7개 투자자 합산 (신 버전) 또는 "기관합계"(구 버전)
        if "기관합계" in flow.columns:
            panel["inst_net"] = flow["기관합계"]
        else:
            panel["inst_net"] = sum(
                _series_or_zero(flow, c) for c in INSTITUTION_COMPONENTS
            )

        panel["indiv_net"] = _series_or_zero(flow, "개인")
        panel["finance_net"] = _series_or_zero(flow, "금융투자")
    else:
        for col in ("foreign_net", "inst_net", "indiv_net", "finance_net"):
            panel[col] = 0.0

    # days 인덱스로 reindex (없는 날은 NaN → 0)
    panel = panel.reindex(days).fillna(0.0)
    return panel
