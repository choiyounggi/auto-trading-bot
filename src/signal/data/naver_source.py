"""네이버 금융 frgn 페이지 fallback.

한계:
  - 외국인 + 기관 순매매량(주)만 제공. 개인/금융투자 분리 불가.
  - 단위는 "주"(quantity). 거래대금 환산은 종가 × 수량으로 근사.
  - 종가, 등락률, 거래량 동시 제공 → 가격 신호도 같이 처리.

따라서 이 fallback에서는 B3(개인 강매도)와 B4(금융투자 편중)는 0(비활성).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15"
BASE = "https://finance.naver.com/item/frgn.naver"


def _cache_path(ticker: str, start: str, end: str) -> Path:
    return CACHE_DIR / f"naver_{ticker}_{start}_{end}.parquet"


def _parse_int(s: str) -> int:
    s = s.replace(",", "").replace(" ", "").strip()
    if not s or s in {"-", "—"}:
        return 0
    sign = 1
    if s.startswith("+"):
        s = s[1:]
    elif s.startswith("-"):
        sign = -1
        s = s[1:]
    try:
        return sign * int(s)
    except ValueError:
        return 0


def _fetch_page(ticker: str, page: int) -> list[dict]:
    """네이버 frgn 페이지 1개 파싱. 일별 dict 리스트 반환 (최신 → 과거)."""
    r = requests.get(
        BASE,
        params={"code": ticker, "page": page},
        headers={"User-Agent": UA, "Referer": "https://finance.naver.com/"},
        timeout=15,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    tables = soup.select("table.type2")
    if len(tables) < 2:
        return []
    daily = tables[1]
    rows = []
    for tr in daily.select("tr"):
        cells = [td.get_text(strip=True) for td in tr.select("td")]
        if len(cells) < 9:
            continue
        date_s = cells[0]
        if not re.match(r"^\d{4}\.\d{2}\.\d{2}$", date_s):
            continue
        rows.append({
            "date": datetime.strptime(date_s, "%Y.%m.%d").strftime("%Y%m%d"),
            "종가": _parse_int(cells[1]),
            "등락률_str": cells[3],
            "거래량": _parse_int(cells[4]),
            "기관_순매매수량": _parse_int(cells[5]),
            "외국인_순매매수량": _parse_int(cells[6]),
        })
    return rows


def _parse_pct(s: str) -> float:
    s = s.replace("%", "").strip()
    sign = 1
    if s.startswith("+"):
        s = s[1:]
    elif s.startswith("-"):
        sign = -1
        s = s[1:]
    try:
        return sign * float(s)
    except ValueError:
        return 0.0


def fetch_ticker_panel_naver(ticker: str, days: list[str]) -> pd.DataFrame:
    """네이버 frgn 기반 패널.
    columns: 종가, 거래량, 등락률, foreign_net, inst_net, indiv_net(=0), finance_net(=0)
    foreign_net/inst_net 단위는 "원(거래대금)" 으로 통일 (종가 × 수량 근사).
    """
    cache = _cache_path(ticker, days[0], days[-1])
    if cache.exists():
        try:
            return pd.read_parquet(cache)
        except Exception:
            pass

    needed = set(days)
    rows: list[dict] = []
    seen: set[str] = set()
    for page in range(1, 6):  # 5페이지 = ~50일
        page_rows = _fetch_page(ticker, page)
        if not page_rows:
            break
        for r in page_rows:
            if r["date"] in seen:
                continue
            seen.add(r["date"])
            if r["date"] >= days[0]:
                rows.append(r)
        last_date = page_rows[-1]["date"]
        if last_date < days[0]:
            break
        if needed.issubset(seen):
            break
        time.sleep(0.3)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).sort_values("date").set_index("date")
    df["등락률"] = df["등락률_str"].apply(_parse_pct)
    df["foreign_net"] = (df["외국인_순매매수량"] * df["종가"]).astype(float)
    df["inst_net"] = (df["기관_순매매수량"] * df["종가"]).astype(float)
    df["indiv_net"] = 0.0
    df["finance_net"] = 0.0
    out = df[["종가", "거래량", "등락률", "foreign_net", "inst_net", "indiv_net", "finance_net"]]
    out = out.reindex(days).fillna(0.0)

    try:
        out.to_parquet(cache)
    except Exception as e:
        log.warning("naver cache write fail: %s", e)
    return out
