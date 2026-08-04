"""거래 가능 종목 필터 — 관리/거래정지/시총 등. MCP 호출 stub.

실 동작은 KIS(한국투자증권) REST 응답 포맷 확정 후 구현.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class TradeabilityCheck:
    ticker: str
    tradeable: bool
    reasons: list[str]


async def check_tradeable(
    client,  # KiwoomMcpClient
    ticker: str,
    min_market_cap_eok: int = 5000,
    exclude_tickers: set[str] | None = None,
) -> TradeabilityCheck:
    """
    절대 차단 조건 검사. 하나라도 위반 시 tradeable=False.
    - 관리종목 / 투자유의/경고/위험 / 거래정지 / ETN / ELW
    - 시총 < min_market_cap_eok
    - exclude_tickers에 명시된 종목

    실 구현은 MCP 응답 포맷 확정 후. 지금은 exclude만 체크.
    """
    reasons: list[str] = []

    if exclude_tickers and ticker in exclude_tickers:
        reasons.append(f"explicit exclude: {ticker}")

    # TODO Phase 3: MCP 호출로 종목 메타 조회
    # info = await client.get_stock_info(ticker)
    # if info.get("is_managed"): reasons.append("관리종목")
    # if info.get("is_warning"): reasons.append("투자유의/경고/위험")
    # if info.get("is_suspended"): reasons.append("거래정지")
    # if info.get("market") in ("ETN", "ELW"): reasons.append(f"시장: {info['market']}")
    # if info.get("market_cap_eok", 0) < min_market_cap_eok:
    #     reasons.append(f"시총 < {min_market_cap_eok}억")

    return TradeabilityCheck(ticker=ticker, tradeable=not reasons, reasons=reasons)
