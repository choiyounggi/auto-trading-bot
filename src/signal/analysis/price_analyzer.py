"""가격/거래량 패턴 분석 — A4, B2."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class PriceSignals:
    a4_volume_spike: bool
    b2_already_pumped: bool
    volume_ratio: float
    return_5d: float


def analyze_ticker_price(panel: pd.DataFrame, params: dict) -> PriceSignals:
    if panel.empty or len(panel) < 6:
        return PriceSignals(False, False, 0.0, 0.0)

    last_volume = float(panel["거래량"].iloc[-1])
    avg_volume = float(panel["거래량"].tail(20).mean())
    vol_ratio = last_volume / avg_volume if avg_volume > 0 else 0.0

    spike_ratio = float(params.get("volume_spike_ratio", 1.5))
    a4 = vol_ratio >= spike_ratio

    # B2: 5일 누적 수익률
    tail = panel.tail(6)
    base = tail["종가"].iloc[0]
    last = tail["종가"].iloc[-1]
    ret_5d = float((last / base - 1) * 100) if base > 0 else 0.0

    pumped_pct = float(params.get("pumped_return_pct", 15.0))
    b2 = ret_5d >= pumped_pct

    return PriceSignals(
        a4_volume_spike=bool(a4),
        b2_already_pumped=bool(b2),
        volume_ratio=vol_ratio,
        return_5d=ret_5d,
    )
