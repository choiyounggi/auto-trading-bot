"""해외주식 일봉 데이터 소스 (yfinance 기반).

KIS 해외주식 주문은 stock-trader가 별도로 담당한다. 이 모듈은 신호 생성용
OHLCV 패널만 제공하며, 국내 KRX 패널과 같은 컬럼명으로 정규화한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
import yaml

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OverseasTicker:
    symbol: str
    name: str
    exchange: str = "NASD"          # KIS 주문용 거래소 코드
    quote_exchange: str = "NAS"     # KIS 시세용 거래소 코드
    currency: str = "USD"
    yf_symbol: str | None = None
    market: str = "US"
    benchmark_symbol: str = "SPY"
    benchmark_exchange: str = "AMEX"
    benchmark_quote_exchange: str = "AMS"
    sector_etf: str | None = None
    earnings_date: str | None = None

    @property
    def yfinance_symbol(self) -> str:
        return self.yf_symbol or self.symbol


def load_overseas_watchlist(path: Path | str) -> list[OverseasTicker]:
    """config/overseas_watchlist.yaml 로드. enabled=false면 빈 리스트."""
    p = Path(path)
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not raw.get("enabled", False):
        return []

    out: list[OverseasTicker] = []
    for row in raw.get("tickers", []) or []:
        if isinstance(row, str):
            row = {"symbol": row, "name": row}
        symbol = str(row.get("symbol", "")).strip().upper()
        if not symbol:
            continue
        out.append(OverseasTicker(
            symbol=symbol,
            name=str(row.get("name") or symbol),
            exchange=str(row.get("exchange") or "NASD").strip().upper(),
            quote_exchange=str(row.get("quote_exchange") or row.get("quote_excd") or "NAS").strip().upper(),
            currency=str(row.get("currency") or "USD").strip().upper(),
            yf_symbol=str(row.get("yf_symbol") or symbol).strip(),
            market=str(row.get("market") or "US").strip().upper(),
            benchmark_symbol=str(row.get("benchmark_symbol") or "SPY").strip().upper(),
            benchmark_exchange=str(row.get("benchmark_exchange") or "AMEX").strip().upper(),
            benchmark_quote_exchange=str(row.get("benchmark_quote_exchange") or "AMS").strip().upper(),
            sector_etf=(str(row.get("sector_etf")).strip().upper() if row.get("sector_etf") else None),
            earnings_date=(str(row.get("earnings_date")).strip() if row.get("earnings_date") else None),
        ))
    return out


def metadata_by_symbol(tickers: Iterable[OverseasTicker]) -> dict[str, dict]:
    """strategy_signals에 병합할 해외주식 메타데이터."""
    meta: dict[str, dict] = {}
    for t in tickers:
        meta[t.symbol] = {
            "asset_class": "overseas_stock",
            "market": t.market,
            "exchange": t.exchange,
            "quote_exchange": t.quote_exchange,
            "currency": t.currency,
            "broker_symbol": t.symbol,
            "price_scale": 100,
            "benchmark_symbol": t.benchmark_symbol,
            "benchmark_exchange": t.benchmark_exchange,
            "benchmark_quote_exchange": t.benchmark_quote_exchange,
            "sector_etf": t.sector_etf,
            "earnings_date": t.earnings_date,
        }
    return meta


def fetch_yfinance_panel_symbol(
    yf_symbol: str,
    symbol: str,
    end: date,
    lookback: int,
    currency: str = "USD",
    scale_prices: bool = True,
) -> pd.DataFrame:
    """임의 yfinance 심볼을 내부 패널 형식으로 조회.

    scale_prices=True면 가격을 minor unit(USD cent)으로 변환한다. VIX 같은
    지표는 scale_prices=False로 사용한다.
    """
    item = OverseasTicker(symbol=symbol, name=symbol, currency=currency, yf_symbol=yf_symbol)
    panel = fetch_overseas_panel(item, end, lookback)
    if not scale_prices and currency.upper() == "USD" and not panel.empty:
        for col in [c for c in ["시가", "고가", "저가", "종가"] if c in panel.columns]:
            panel[col] = panel[col].astype(float) / 100.0
    return panel


def fetch_overseas_panel(ticker: OverseasTicker, end: date, lookback: int) -> pd.DataFrame:
    """해외 일봉 패널을 국내 패널 컬럼명으로 정규화.

    Returns columns: 종가, 시가, 고가, 저가, 거래량, 등락률, foreign_net, inst_net
    """
    try:
        import yfinance as yf
    except Exception as e:  # pragma: no cover - dependency/runtime guard
        log.warning("yfinance import 실패: %s", e)
        return pd.DataFrame()

    # 휴장/주말을 감안해 넉넉히 조회
    start = end - timedelta(days=max(lookback * 3, lookback + 30))
    try:
        df = yf.download(
            ticker.yfinance_symbol,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=True,
            progress=False,
            threads=False,
        )
    except Exception as e:
        log.warning("yfinance %s fetch 실패: %s", ticker.symbol, e)
        return pd.DataFrame()

    if df is None or df.empty:
        log.warning("yfinance %s 데이터 없음", ticker.symbol)
        return pd.DataFrame()

    # yfinance가 단일 종목에도 MultiIndex를 반환하는 버전 대응
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    rename = {
        "Open": "시가",
        "High": "고가",
        "Low": "저가",
        "Close": "종가",
        "Volume": "거래량",
    }
    panel = df.rename(columns=rename)
    keep = [c for c in ["시가", "고가", "저가", "종가", "거래량"] if c in panel.columns]
    panel = panel[keep].copy()
    if "종가" not in panel.columns:
        return pd.DataFrame()

    for col in keep:
        panel[col] = pd.to_numeric(panel[col], errors="coerce")
    panel = panel.dropna(subset=["종가"]).sort_index()

    # 해외 가격은 SQLite 기존 정수 가격 스키마와 호환되도록 minor unit(USD cent)로 저장한다.
    # 수익률/ATR 비율 계산은 선형 스케일에 불변이다.
    if ticker.currency.upper() == "USD":
        for col in [c for c in ["시가", "고가", "저가", "종가"] if c in panel.columns]:
            panel[col] = (panel[col] * 100).round().astype(int)

    panel["등락률"] = panel["종가"].pct_change().fillna(0.0) * 100.0
    panel["foreign_net"] = 0
    panel["inst_net"] = 0
    panel["ticker"] = ticker.symbol
    panel["market"] = ticker.market
    panel["currency"] = ticker.currency
    return panel.tail(lookback).reset_index(drop=True)
