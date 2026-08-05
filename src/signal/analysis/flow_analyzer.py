"""투자자별 수급(net buy) 패턴 분석 — A1, A2, A3, B1, B3, B4."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class FlowSignals:
    a1_inst_foreign_both: bool
    a2_consecutive_buying: bool
    a3_decoupling_score: float       # 0.0 ~ 1.0 (외부에서 시장 전체와 비교 후 활성화)
    b1_one_day_only: bool
    b3_only_inst_buying: bool
    b4_only_finance: bool


def analyze_ticker_flow(panel: pd.DataFrame, params: dict) -> FlowSignals:
    """
    panel: build_ticker_panel 결과 (date 오름차순 index).
    columns: foreign_net, inst_net, indiv_net, finance_net
    """
    if panel.empty:
        return FlowSignals(False, False, 0.0, False, False, False)

    last = panel.iloc[-1]
    n = int(params.get("consecutive_days", 3))
    recent = panel.tail(n)

    # A1: 당일 기관 + 외국인 동시 순매수
    a1 = last["inst_net"] > 0 and last["foreign_net"] > 0

    # A2: 외국인 OR 기관이 N일 연속 순매수
    a2 = bool((recent["inst_net"] > 0).all() or (recent["foreign_net"] > 0).all())

    # B1: 3일 윈도우 중 기관이 +1일만 (그리고 당일이 그 +1일)
    pos_days = int((recent["inst_net"] > 0).sum())
    b1 = pos_days == 1 and last["inst_net"] > 0

    # B3: 외국인 음 + 개인 강한 음 + 기관만 양
    indiv_strong_ratio = float(params.get("individual_strong_sell_ratio", 2.0))
    foreign_neg = last["foreign_net"] < 0
    indiv_strong_neg = (
        last["indiv_net"] < 0
        and abs(last["indiv_net"]) >= abs(last["foreign_net"]) * indiv_strong_ratio
    )
    inst_pos = last["inst_net"] > 0
    b3 = bool(foreign_neg and indiv_strong_neg and inst_pos)

    # B4: 기관 중 금융투자 비중 80%+
    finance_only_ratio = float(params.get("finance_only_ratio", 0.8))
    inst_net = last["inst_net"]
    finance_net = last["finance_net"]
    b4 = bool(
        inst_net > 0
        and finance_net > 0
        and (finance_net / inst_net) >= finance_only_ratio
    )

    # A3 점수는 cross-sectional 비교가 필요 — 여기선 누적값만 계산하고 외부에서 rank
    return FlowSignals(
        a1_inst_foreign_both=bool(a1),
        a2_consecutive_buying=bool(a2),
        a3_decoupling_score=0.0,  # 후처리
        b1_one_day_only=bool(b1),
        b3_only_inst_buying=bool(b3),
        b4_only_finance=bool(b4),
    )


def cumulative_netbuy(panel: pd.DataFrame, window: int) -> float:
    """최근 window일 외국인+기관 합산 순매수."""
    if panel.empty:
        return 0.0
    tail = panel.tail(window)
    return float((tail["foreign_net"] + tail["inst_net"]).sum())


def cumulative_return(panel: pd.DataFrame, window: int) -> float:
    """최근 window일 누적 수익률 % (단순 종가 대비)."""
    if panel.empty or len(panel) < 2:
        return 0.0
    tail = panel.tail(window + 1)
    if len(tail) < 2 or tail["종가"].iloc[0] == 0:
        return 0.0
    return float((tail["종가"].iloc[-1] / tail["종가"].iloc[0] - 1) * 100)
