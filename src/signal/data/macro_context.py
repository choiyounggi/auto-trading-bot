"""거시 환경 스냅샷 — yfinance로 주요 글로벌 지수 + 환율 + 원자재.

LLM 분석에 시장 전체 흐름을 주입하기 위한 컨텍스트.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

INDICES: dict[str, str] = {
    "다우": "^DJI",
    "나스닥": "^IXIC",
    "S&P500": "^GSPC",
    "코스피": "^KS11",
    "코스닥": "^KQ11",
    "원/달러": "KRW=X",
    "WTI유가": "CL=F",
    "VIX(공포지수)": "^VIX",
}


@dataclass
class IndexSnapshot:
    label: str
    symbol: str
    close: float
    d_change_pct: float        # 전일 대비
    w_change_pct: float        # 5거래일 누적
    rows: int


def fetch_macro_snapshot() -> list[IndexSnapshot]:
    """전 종목 1회 fetch. 실패한 항목은 결과에서 제외."""
    try:
        import yfinance as yf
    except Exception as e:
        log.warning("yfinance import 실패: %s", e)
        return []

    out: list[IndexSnapshot] = []
    for label, sym in INDICES.items():
        try:
            h = yf.Ticker(sym).history(period="10d", interval="1d")
            if h is None or h.empty or len(h) < 2:
                log.warning("macro %s(%s) 데이터 부족", label, sym)
                continue
            last = h.iloc[-1]
            prev = h.iloc[-2]
            d_chg = (last["Close"] / prev["Close"] - 1) * 100
            base_idx = max(0, len(h) - 6)  # 5일 전
            w_base = h.iloc[base_idx]
            w_chg = (last["Close"] / w_base["Close"] - 1) * 100
            out.append(IndexSnapshot(
                label=label,
                symbol=sym,
                close=float(last["Close"]),
                d_change_pct=float(d_chg),
                w_change_pct=float(w_chg),
                rows=len(h),
            ))
        except Exception as e:
            log.warning("macro %s(%s) 실패: %s", label, sym, e)
    return out


def render_macro_block(snaps: list[IndexSnapshot]) -> str:
    """LLM 프롬프트용 텍스트 블록."""
    if not snaps:
        return "[글로벌 거시 환경] (데이터 없음)\n"
    lines = ["[글로벌 거시 환경 — 전일 대비 / 최근 5일 누적]"]
    for s in snaps:
        lines.append(
            f"  {s.label:<12} {s.close:>10,.2f}  "
            f"d {s.d_change_pct:+5.2f}%   5d {s.w_change_pct:+5.2f}%"
        )
    return "\n".join(lines) + "\n"
