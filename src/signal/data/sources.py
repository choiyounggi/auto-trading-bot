"""데이터 소스 어댑터: KRX 우선, 실패 시 네이버 fallback."""
from __future__ import annotations

import logging
import os

import pandas as pd

from src.signal.data import pykrx_source, naver_source

log = logging.getLogger(__name__)


def _krx_logged_in() -> bool:
    return bool(os.getenv("KRX_ID") and os.getenv("KRX_PW"))


def fetch_ticker_panel(ticker: str, days: list[str]) -> tuple[pd.DataFrame, str]:
    """
    Returns (panel, source_name)
    source_name: 'krx' | 'naver' | 'empty'
    """
    # 1) KRX (수급 endpoint는 로그인 필요. 로그인 자격 없으면 빈 응답)
    if _krx_logged_in():
        try:
            panel = pykrx_source.fetch_ticker_panel(ticker, days)
            # 수급 합계가 모두 0이면 사실상 실패로 간주 (로그인 실패 등)
            if not panel.empty and (panel["foreign_net"].abs().sum() + panel["inst_net"].abs().sum()) > 0:
                return panel, "krx"
            log.warning("krx panel %s: 수급 zero — fallback to naver", ticker)
        except Exception as e:
            log.warning("krx panel %s 실패: %s — naver fallback", ticker, e)

    # 2) 네이버 fallback
    try:
        panel = naver_source.fetch_ticker_panel_naver(ticker, days)
        if not panel.empty:
            return panel, "naver"
    except Exception as e:
        log.warning("naver panel %s 실패: %s", ticker, e)

    return pd.DataFrame(), "empty"
