"""신호 점수 통합 — BUY/CAUTION 판정."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .flow_analyzer import FlowSignals
from .price_analyzer import PriceSignals


@dataclass
class TickerSignal:
    ticker: str
    name: str
    score: int = 0
    triggers: List[str] = field(default_factory=list)  # human-readable 코드들
    detail: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        if self.score >= 0:
            return "BUY"
        return "CAUTION"


def combine(
    ticker: str,
    name: str,
    flow: FlowSignals,
    price: PriceSignals,
    weights_buy: dict,
    weights_caution: dict,
    a3_active: bool = False,
) -> TickerSignal:
    """flow + price 결과를 종합. a3_active는 cross-sectional 후처리 결과."""
    sig = TickerSignal(ticker=ticker, name=name)

    if flow.a1_inst_foreign_both:
        sig.score += int(weights_buy.get("A1_inst_foreign_both", 0))
        sig.triggers.append("A1:기관+외국인 동시매수")
    if flow.a2_consecutive_buying:
        sig.score += int(weights_buy.get("A2_consecutive_buying", 0))
        sig.triggers.append("A2:연속순매수")
    if a3_active:
        sig.score += int(weights_buy.get("A3_decoupling", 0))
        sig.triggers.append("A3:수급-가격 디커플링")
    if price.a4_volume_spike:
        sig.score += int(weights_buy.get("A4_volume_spike", 0))
        sig.triggers.append(f"A4:거래량x{price.volume_ratio:.1f}")

    if flow.b1_one_day_only:
        sig.score += int(weights_caution.get("B1_one_day_only", 0))
        sig.triggers.append("B1:기관 하루만")
    if price.b2_already_pumped:
        sig.score += int(weights_caution.get("B2_already_pumped", 0))
        sig.triggers.append(f"B2:5일+{price.return_5d:.1f}%")
    if flow.b3_only_inst_buying:
        sig.score += int(weights_caution.get("B3_only_inst_buying", 0))
        sig.triggers.append("B3:기관만매수")
    if flow.b4_only_finance:
        sig.score += int(weights_caution.get("B4_only_금융투자", 0))
        sig.triggers.append("B4:금융투자편중")

    sig.detail = {
        "volume_ratio": round(price.volume_ratio, 2),
        "return_5d": round(price.return_5d, 2),
    }
    return sig
