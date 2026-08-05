"""공매도 잔고 — pykrx로 종목별 공매도 잔고 비중 및 변화 추출.

LLM 분석 컨텍스트 보강:
  - BUY 시그널 + 잔고 감소 = 숏 커버링 진행 → 단기 강세 확률 ↑
  - BUY 시그널 + 잔고 증가 = 기관/외인 하락 베팅 강화 → 신뢰도 톤다운

pykrx get_shorting_balance_by_date 출력 컬럼:
  [공매도잔고, 상장주식수, 공매도금액, 시가총액, 비중]
  비중 = 상장주식수 대비 공매도잔고 % (D+2 공개)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

log = logging.getLogger(__name__)


@dataclass
class ShortBalance:
    ticker: str
    latest_pct: float
    pct_5d_change: float
    pct_20d_change: float
    latest_date: str
    days_lag: int


def fetch_short_balance(ticker: str, lookback_days: int = 45) -> ShortBalance | None:
    try:
        from pykrx import stock
    except Exception as e:
        log.warning("pykrx import 실패: %s", e)
        return None

    today = datetime.now()
    end = today.strftime("%Y%m%d")
    start = (today - timedelta(days=lookback_days)).strftime("%Y%m%d")

    try:
        df = stock.get_shorting_balance_by_date(start, end, ticker)
    except Exception as e:
        log.warning("공매도잔고 %s 실패: %s", ticker, e)
        return None

    if df is None or df.empty:
        log.info("공매도잔고 %s 데이터 없음", ticker)
        return None

    col = None
    for c in ("비중", "잔고비중", "공매도비중"):
        if c in df.columns:
            col = c
            break
    if col is None:
        log.warning("공매도잔고 %s 비중 컬럼 없음 (columns=%s)", ticker, list(df.columns))
        return None

    series = df[col].dropna()
    if series.empty:
        return None

    latest = float(series.iloc[-1])
    d5 = float(series.iloc[-6]) if len(series) >= 6 else float(series.iloc[0])
    d20 = float(series.iloc[-21]) if len(series) >= 21 else float(series.iloc[0])

    last_idx = series.index[-1]
    latest_date_str = last_idx.strftime("%Y-%m-%d") if hasattr(last_idx, "strftime") else str(last_idx)
    try:
        last_date = last_idx.date() if hasattr(last_idx, "date") else datetime.strptime(str(last_idx)[:10], "%Y-%m-%d").date()
        lag = (today.date() - last_date).days
    except Exception:
        lag = -1

    return ShortBalance(
        ticker=ticker,
        latest_pct=latest,
        pct_5d_change=latest - d5,
        pct_20d_change=latest - d20,
        latest_date=latest_date_str,
        days_lag=lag,
    )


def render_short_block(sb: ShortBalance | None) -> str:
    if not sb:
        return ""
    a5 = "↑" if sb.pct_5d_change > 0 else ("↓" if sb.pct_5d_change < 0 else "→")
    a20 = "↑" if sb.pct_20d_change > 0 else ("↓" if sb.pct_20d_change < 0 else "→")
    return (
        f"[공매도 잔고 — {sb.latest_date} 기준 (D-{sb.days_lag}, KRX D+2 공개 지연)]\n"
        f"  현재 비중: {sb.latest_pct:.2f}% (상장주식수 대비)\n"
        f"  5거래일 변화: {a5} {sb.pct_5d_change:+.2f}%p\n"
        f"  20거래일 변화: {a20} {sb.pct_20d_change:+.2f}%p\n"
        f"  해석: 잔고↑ + 가격↑ = 숏 스퀴즈 위험 / 잔고↓ + 가격↑ = 숏 커버링"
    )
