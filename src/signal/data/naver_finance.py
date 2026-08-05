"""네이버 finance 통합 모듈 — KOSPI/KOSDAQ 가격 + 매매주체 + 시황 기사.

3가지 데이터 소스:
  A. 시황 기사 헤드라인       — sise_index.naver 페이지 스크래핑
  B. 시장 매매주체 일별 순매수 — sise_index_invest_day.naver 스크래핑
  C. 지수 OHLCV               — siseJson.naver JSON endpoint (비공식 API, 안정)

yfinance가 query1.finance.yahoo.com 서버 의존인 반면, 이 모듈은 국내 서버 →
latency ↓ + Yahoo 정책 변경 영향 X. yfinance와 병행 동작 (이중화).

dependency 추가 필요:
  pip install beautifulsoup4 lxml
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import requests

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Apple Silicon Mac OS X 14_0) stock-signal-bot/1.0"
TIMEOUT = 8
INDEX_CODES = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ"}


# ============================================================
# C. 지수 OHLCV — siseJson.naver
# ============================================================

@dataclass
class NaverIndexQuote:
    code: Literal["KOSPI", "KOSDAQ"]
    close: float
    d_change_pct: float        # 전일 대비
    w_change_pct: float        # 5거래일 누적
    today_open: float
    today_high: float
    today_low: float
    volume: int
    foreign_ratio: float = 0.0  # 외국인 소진율 (지수 페이지 default 0)


def fetch_index_ohlcv(code: str = "KOSPI", lookback_days: int = 10) -> NaverIndexQuote | None:
    """siseJson.naver JSON-like 응답 파싱. 최근 N 거래일 fetch."""
    if code not in INDEX_CODES:
        log.warning("unsupported index code: %s", code)
        return None

    today = datetime.now().strftime("%Y%m%d")
    url = (
        f"https://api.finance.naver.com/siseJson.naver?"
        f"symbol={code}&requestType=1&startTime=20260101&endTime={today}&timeframe=day"
    )

    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("naver siseJson %s HTTP %d", code, r.status_code)
            return None
        # 응답 형식: "[['날짜','시가','고가','저가','종가','거래량','외국인소진율'], [...], ...]"
        # JSON 표준 아님 — single quote 사용. 정규화 후 파싱.
        text = r.text.strip()
        normalized = re.sub(r"'", '"', text)
        rows = json.loads(normalized)
    except (requests.RequestException, json.JSONDecodeError, ValueError) as e:
        log.warning("naver siseJson %s 실패: %s", code, e)
        return None

    if not rows or len(rows) < 2:
        return None

    # rows[0] = header
    data = rows[1:]
    if not data:
        return None
    data = data[-lookback_days:]  # 최근 N
    last = data[-1]
    if len(data) >= 2:
        prev = data[-2]
    else:
        prev = last
    week_base = data[max(0, len(data) - 6)] if len(data) >= 6 else data[0]

    try:
        last_close = float(last[4])
        prev_close = float(prev[4])
        d_chg = (last_close / prev_close - 1) * 100 if prev_close else 0.0
        w_base = float(week_base[4])
        w_chg = (last_close / w_base - 1) * 100 if w_base else 0.0
        return NaverIndexQuote(
            code=code,  # type: ignore[arg-type]
            close=last_close,
            d_change_pct=d_chg,
            w_change_pct=w_chg,
            today_open=float(last[1]),
            today_high=float(last[2]),
            today_low=float(last[3]),
            volume=int(float(last[5])) if last[5] else 0,
            foreign_ratio=float(last[6]) if len(last) > 6 and last[6] else 0.0,
        )
    except (IndexError, ValueError, TypeError) as e:
        log.warning("naver siseJson %s 파싱 실패: %s", code, e)
        return None


# ============================================================
# B. 시장 매매주체 일별 순매수
# ============================================================

@dataclass
class NaverInvestorFlow:
    code: Literal["KOSPI", "KOSDAQ"]
    flows: list[dict] = field(default_factory=list)  # 각 일자별 {date, individual, foreign, institution}


def fetch_invest_day(code: str = "KOSPI", days: int = 5) -> NaverInvestorFlow:
    """
    sise_index_invest_day.naver 스크래핑.
    페이지에 일별 (개인/외국인/기관) 순매수 테이블.
    """
    out = NaverInvestorFlow(code=code)  # type: ignore[arg-type]
    if code not in INDEX_CODES:
        return out

    url = f"https://finance.naver.com/sise/sise_index_invest_day.naver?code={code}"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        if r.status_code != 200:
            return out
    except requests.RequestException as e:
        log.warning("naver invest_day %s 실패: %s", code, e)
        return out

    try:
        from bs4 import BeautifulSoup  # 함수 내부 import — bs4 미설치 시 graceful fallback
    except ImportError:
        log.warning("beautifulsoup4 미설치 — naver_finance.fetch_invest_day skip")
        return out

    soup = BeautifulSoup(r.text, "lxml" if _has_lxml() else "html.parser")
    # 페이지 테이블 구조는 네이버 변경에 따라 달라질 수 있음 — 정확한 selector는 검증 필요
    rows = soup.select("table.type_1 tr")
    parsed = 0
    for tr in rows:
        cols = [c.text.strip() for c in tr.select("td")]
        if len(cols) < 4 or not cols[0]:
            continue
        try:
            day_str = cols[0]
            individual = _won_str_to_int(cols[1])
            foreign = _won_str_to_int(cols[2])
            institution = _won_str_to_int(cols[3])
            out.flows.append({
                "date": day_str,
                "individual_net_won": individual,
                "foreign_net_won": foreign,
                "institution_net_won": institution,
            })
            parsed += 1
            if parsed >= days:
                break
        except (ValueError, IndexError):
            continue

    return out


def _has_lxml() -> bool:
    try:
        import lxml  # noqa: F401
        return True
    except ImportError:
        return False


def _won_str_to_int(s: str) -> int:
    """'+1,234억' 또는 '-12,345' 같은 문자열 → 정수 원. 단위 추정."""
    s = s.replace(",", "").replace("+", "").strip()
    if not s or s == "-":
        return 0
    sign = -1 if s.startswith("-") else 1
    s = s.lstrip("-")
    multiplier = 1
    if s.endswith("억"):
        multiplier = 100_000_000
        s = s[:-1]
    elif s.endswith("만"):
        multiplier = 10_000
        s = s[:-1]
    try:
        return sign * int(float(s) * multiplier)
    except ValueError:
        return 0


# ============================================================
# A. 시황 기사 헤드라인
# ============================================================

@dataclass
class NaverHeadline:
    code: Literal["KOSPI", "KOSDAQ"]
    title: str
    url: str
    source: str = "naver_finance"


def fetch_market_headlines(code: str = "KOSPI", limit: int = 5) -> list[NaverHeadline]:
    """
    sise_index.naver 페이지 우측/하단 시황 기사 영역 스크래핑.
    Brave Search보다 시황 specific — 네이버 큐레이션.
    """
    if code not in INDEX_CODES:
        return []

    url = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
    except requests.RequestException as e:
        log.warning("naver headlines %s 실패: %s", code, e)
        return []

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.warning("beautifulsoup4 미설치 — fetch_market_headlines skip")
        return []

    soup = BeautifulSoup(r.text, "lxml" if _has_lxml() else "html.parser")
    out: list[NaverHeadline] = []

    # 네이버 finance 페이지의 뉴스 영역 selector 후보들 — 페이지 변경 시 우선순위로 시도
    for selector in (
        ".news_lst a",
        ".section_strategy .news_lst a",
        ".aside_area .news_lst a",
        "a[href*='news_read']",
    ):
        for a in soup.select(selector):
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if not title or len(title) < 5:
                continue
            full_url = href if href.startswith("http") else f"https://finance.naver.com{href}"
            out.append(NaverHeadline(code=code, title=title, url=full_url))  # type: ignore[arg-type]
            if len(out) >= limit:
                return out
        if out:
            break

    return out


# ============================================================
# 통합 fetch — macro_context에서 한 번에 호출
# ============================================================

def fetch_all_kospi_kosdaq(headlines_per_market: int = 5, invest_days: int = 5) -> dict:
    """
    macro_context에서 호출. KOSPI/KOSDAQ 통합 데이터.

    Returns:
        {
            "kospi_quote": NaverIndexQuote | None,
            "kosdaq_quote": NaverIndexQuote | None,
            "kospi_flow":   NaverInvestorFlow,
            "kosdaq_flow":  NaverInvestorFlow,
            "headlines":    list[NaverHeadline],  # KOSPI + KOSDAQ 합산
        }
    """
    out: dict = {}
    out["kospi_quote"] = fetch_index_ohlcv("KOSPI")
    out["kosdaq_quote"] = fetch_index_ohlcv("KOSDAQ")
    out["kospi_flow"] = fetch_invest_day("KOSPI", days=invest_days)
    out["kosdaq_flow"] = fetch_invest_day("KOSDAQ", days=invest_days)
    out["headlines"] = (
        fetch_market_headlines("KOSPI", limit=headlines_per_market)
        + fetch_market_headlines("KOSDAQ", limit=headlines_per_market)
    )
    return out


def render_naver_block(data: dict) -> str:
    """LLM 프롬프트용 텍스트 블록."""
    lines: list[str] = []

    kq, kdq = data.get("kospi_quote"), data.get("kosdaq_quote")
    if kq or kdq:
        lines.append("[국내 지수 — 네이버 finance]")
        for q in (kq, kdq):
            if q is None:
                continue
            lines.append(
                f"  {q.code:<7} {q.close:>9,.2f}  "
                f"d {q.d_change_pct:+5.2f}%   5d {q.w_change_pct:+5.2f}%   "
                f"고가 {q.today_high:,.2f} / 저가 {q.today_low:,.2f}"
            )
        lines.append("")

    for label, key in (("KOSPI", "kospi_flow"), ("KOSDAQ", "kosdaq_flow")):
        flow = data.get(key)
        if flow and flow.flows:
            lines.append(f"[{label} 매매주체 일별 (최근 {len(flow.flows)}일)]")
            lines.append(f"  {'일자':<12} {'개인':>10} {'외국인':>10} {'기관':>10}  (단위: 억)")
            for f in flow.flows:
                lines.append(
                    f"  {f['date']:<12} "
                    f"{f['individual_net_won']/1e8:>+10,.0f} "
                    f"{f['foreign_net_won']/1e8:>+10,.0f} "
                    f"{f['institution_net_won']/1e8:>+10,.0f}"
                )
            lines.append("")

    headlines = data.get("headlines") or []
    if headlines:
        lines.append("[네이버 시황 기사 — 시장 큐레이션, Brave Search보다 specific]")
        for h in headlines[:10]:
            lines.append(f"  - [{h.code}] {h.title}")
        lines.append("")

    return "\n".join(lines)
