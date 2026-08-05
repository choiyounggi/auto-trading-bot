"""신호 점수화 단위 테스트."""
from __future__ import annotations

import pandas as pd
import pytest

from src.signal.analysis.flow_analyzer import (
    analyze_ticker_flow,
    cumulative_netbuy,
    cumulative_return,
)
from src.signal.analysis.price_analyzer import PriceSignals, analyze_ticker_price
from src.signal.analysis.signal_engine import combine


PARAMS = {
    "consecutive_days": 3,
    "accumulate_window": 5,
    "volume_spike_ratio": 1.5,
    "pumped_return_pct": 15.0,
    "individual_strong_sell_ratio": 2.0,
    "finance_only_ratio": 0.8,
}

BUY_W = {
    "A1_inst_foreign_both": 2,
    "A2_consecutive_buying": 3,
    "A3_decoupling": 3,
    "A4_volume_spike": 1,
}

CAUTION_W = {
    "B1_one_day_only": -2,
    "B2_already_pumped": -2,
    "B3_only_inst_buying": -3,
    "B4_only_금융투자": -2,
}


def _panel(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df = df.set_index("date")
    return df


# ---------- 정상 케이스 ----------

def test_a1_a2_a4_buy_combination_normal():
    """기관+외국인 동시 + 3일 연속 + 거래량 급증 → 강한 BUY."""
    rows = []
    for i in range(20):
        rows.append({
            "date": f"2026050{i % 9 + 1}",
            "종가": 10000 + i * 10,
            "거래량": 100000,
            "등락률": 0.1,
            "foreign_net": 1_000_000,
            "inst_net": 1_000_000,
            "indiv_net": -2_000_000,
            "finance_net": 100_000,
        })
    # 마지막 거래량 급증
    rows[-1]["거래량"] = 300_000
    panel = _panel(rows)

    flow = analyze_ticker_flow(panel, PARAMS)
    price = analyze_ticker_price(panel, PARAMS)
    sig = combine("000001", "테스트", flow, price, BUY_W, CAUTION_W)

    assert flow.a1_inst_foreign_both is True
    assert flow.a2_consecutive_buying is True
    assert price.a4_volume_spike is True
    # A1(2) + A2(3) + A4(1) = 6
    assert sig.score == 6
    assert sig.kind == "BUY"
    assert any("A1" in t for t in sig.triggers)


# ---------- 에러/예외 케이스 ----------

def test_empty_panel_returns_zero_signals():
    empty = pd.DataFrame()
    flow = analyze_ticker_flow(empty, PARAMS)
    price = analyze_ticker_price(empty, PARAMS)
    sig = combine("000000", "빈종목", flow, price, BUY_W, CAUTION_W)
    assert sig.score == 0
    assert sig.triggers == []


def test_short_panel_no_volume_spike():
    """6일 미만 패널은 가격 신호 비활성."""
    rows = [{
        "date": f"202605{i:02d}",
        "종가": 10000,
        "거래량": 100000,
        "등락률": 0.0,
        "foreign_net": 0, "inst_net": 0, "indiv_net": 0, "finance_net": 0,
    } for i in range(1, 5)]
    price = analyze_ticker_price(_panel(rows), PARAMS)
    assert price.a4_volume_spike is False
    assert price.b2_already_pumped is False


# ---------- 경계값 / CAUTION 신호 ----------

def test_b3_only_inst_buying_caution():
    """외국인 강한 매도 + 개인 강한 매도 + 기관만 매수 (마지막 날만)."""
    rows = []
    for i in range(20):
        # 19일은 중립 (모든 신호 비활성), 마지막 1일만 B3 패턴
        if i < 19:
            rows.append({
                "date": f"202605{i+1:02d}",
                "종가": 10000, "거래량": 100000, "등락률": 0.0,
                "foreign_net": 0, "inst_net": -100,  # B1, A2 회피용 음수
                "indiv_net": 0, "finance_net": 0,
            })
        else:
            rows.append({
                "date": f"202605{i+1:02d}",
                "종가": 10000, "거래량": 100000, "등락률": 0.0,
                "foreign_net": -1_000_000,
                "inst_net": 500_000,
                "indiv_net": -3_000_000,  # 외국인 매도의 3배
                "finance_net": 100_000,
            })
    panel = _panel(rows)
    flow = analyze_ticker_flow(panel, PARAMS)
    sig = combine("000002", "테스트2", flow, PriceSignals(False, False, 1.0, 0.0), BUY_W, CAUTION_W)
    assert flow.b3_only_inst_buying is True
    # B3(-3) + B1(-2, 마지막 1일만 +) = -5
    assert sig.score <= -5
    assert sig.kind == "CAUTION"
    assert any("B3" in t for t in sig.triggers)


def test_b4_finance_only_caution():
    """기관 중 금융투자가 90%."""
    rows = [{
        "date": f"202605{i+1:02d}",
        "종가": 10000, "거래량": 100000, "등락률": 0.0,
        "foreign_net": 100, "inst_net": 1_000_000,
        "indiv_net": 0, "finance_net": 900_000,
    } for i in range(20)]
    panel = _panel(rows)
    flow = analyze_ticker_flow(panel, PARAMS)
    assert flow.b4_only_finance is True


def test_b2_already_pumped():
    """5일간 +20% 상승."""
    closes = [10000, 10500, 11000, 11500, 12000, 12500]  # +25%
    rows = [{
        "date": f"20260501",  # date 무관, index만 필요
        "종가": c, "거래량": 100000, "등락률": 0.0,
        "foreign_net": 0, "inst_net": 0, "indiv_net": 0, "finance_net": 0,
    } for c in closes]
    # 고유 date 부여
    for i, r in enumerate(rows):
        r["date"] = f"202605{i+1:02d}"
    panel = _panel(rows)
    price = analyze_ticker_price(panel, PARAMS)
    assert price.b2_already_pumped is True
    assert price.return_5d > 15


def test_b1_one_day_only():
    """3일 윈도우 중 마지막 1일만 기관 +."""
    rows = []
    for i in range(20):
        inst = -1_000_000
        if i == 19:
            inst = 500_000
        rows.append({
            "date": f"202605{i+1:02d}",
            "종가": 10000, "거래량": 100000, "등락률": 0.0,
            "foreign_net": 0, "inst_net": inst,
            "indiv_net": 0, "finance_net": 0,
        })
    panel = _panel(rows)
    flow = analyze_ticker_flow(panel, PARAMS)
    assert flow.b1_one_day_only is True


# ---------- 누적/수익률 보조 ----------

def test_cumulative_netbuy_window():
    rows = [{
        "date": f"202605{i+1:02d}",
        "종가": 10000, "거래량": 100000, "등락률": 0.0,
        "foreign_net": 100, "inst_net": 200,
        "indiv_net": 0, "finance_net": 0,
    } for i in range(10)]
    panel = _panel(rows)
    # 최근 5일 (100+200) * 5 = 1500
    assert cumulative_netbuy(panel, 5) == 1500


def test_cumulative_return_basic():
    rows = [{
        "date": f"202605{i+1:02d}",
        "종가": 10000 + i * 1000,  # 10000, 11000, ..., 15000
        "거래량": 100000, "등락률": 0.0,
        "foreign_net": 0, "inst_net": 0, "indiv_net": 0, "finance_net": 0,
    } for i in range(6)]
    panel = _panel(rows)
    # 5일 수익률: 15000/10000 -1 = 50%
    ret = cumulative_return(panel, 5)
    assert ret == pytest.approx(50.0)
