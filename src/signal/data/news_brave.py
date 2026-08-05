"""Brave Search API로 종목 관련 뉴스 검색 (무료 tier)."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)
ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


@dataclass
class NewsItem:
    title: str
    url: str
    description: str
    age: str  # "2 days ago" 등 Brave가 반환하는 상대 표시


def _search_raw(query: str, count: int, freshness: str = "pw") -> list[NewsItem]:
    key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if not key:
        return []
    try:
        r = requests.get(
            ENDPOINT,
            params={
                "q": query,
                "count": count,
                "search_lang": "ko",
                "country": "KR",
                "freshness": freshness,
            },
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
    except Exception as e:
        log.warning("brave search 실패 (%s): %s", query, e)
        return []
    data = r.json()
    out: list[NewsItem] = []
    for x in data.get("web", {}).get("results", []) or []:
        out.append(NewsItem(
            title=str(x.get("title", "")).strip(),
            url=str(x.get("url", "")).strip(),
            description=str(x.get("description", "")).strip(),
            age=str(x.get("age", "")).strip(),
        ))
    time.sleep(0.05)
    return out[:count]


MACRO_QUERIES = [
    "미국 증시 다우 나스닥 종가",
    "코스피 코스닥 외국인 매도 상승 하락",
    "트럼프 OR 연준 OR FOMC OR 금리",
    "유가 환율 OR 원달러 OR 지정학",
]


def search_macro_headlines(count_per_query: int = 3) -> list[NewsItem]:
    """글로벌 거시 헤드라인 — 여러 쿼리 합집합 (URL 중복 제거)."""
    seen_urls: set[str] = set()
    out: list[NewsItem] = []
    for q in MACRO_QUERIES:
        for n in _search_raw(q, count_per_query, freshness="pd"):  # 24h
            if n.url and n.url not in seen_urls:
                seen_urls.add(n.url)
                out.append(n)
    return out


def search_news(stock_name: str, ticker: str = "", count: int = 6) -> list[NewsItem]:
    """종목명 기반 검색. ticker는 호출자 추적용."""
    _ = ticker
    key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if not key:
        log.warning("BRAVE_SEARCH_API_KEY 미설정 — 뉴스 검색 skip")
        return []

    return _search_raw(f"{stock_name} 주가 OR 실적 OR 공시", count, freshness="pw")
