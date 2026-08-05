from __future__ import annotations

import pandas as pd

from src.signal.analysis.strategy_builder import build_strategy_signals, panel_features, value_quality_status


def _panel(closes: list[float]) -> pd.DataFrame:
    rows = []
    for i, close in enumerate(closes):
        rows.append({
            "시가": close - 1,
            "고가": close + 1,
            "저가": close - 2,
            "종가": close,
            "거래량": 1_000_000 + i * 1000,
            "등락률": 0.5,
            "foreign_net": 100_000_000,
            "inst_net": 100_000_000,
            "indiv_net": -100_000_000,
            "finance_net": 10_000_000,
        })
    return pd.DataFrame(rows, index=[f"2025{i:04d}" for i in range(len(rows))])


def test_panel_features_calculates_turtle_inputs():
    panel = _panel([100 + i * 0.1 for i in range(220)] + [140])

    f = panel_features(panel)

    assert f["ma200_available"] is True
    assert f["above_ma200"] is True
    assert f["breakout_high_20"] is True
    assert f["atr20"] > 0
    assert f["turtle_stop_loss_pct"] > 0
    assert f["donchian_low_10"] > 0


def test_build_strategy_signals_adds_turtle_breakout_when_breakout_and_ma200_filter_pass():
    panel = _panel([100 + i * 0.1 for i in range(220)] + [140])

    signals = build_strategy_signals(
        tickers=["005930"],
        names={"005930": "삼성전자"},
        panels={"005930": panel},
        flow_buys=[],
        short_balances={},
        fundamentals={},
    )

    turtle = [s for s in signals if s["strategy_id"] == "turtle_breakout"]
    assert len(turtle) == 1
    assert turtle[0]["ticker"] == "005930"
    assert turtle[0]["strategy_score"] >= 5
    assert turtle[0]["features"]["risk_model"] == "atr20_2x_account_risk"
    assert turtle[0]["panel_summary"]["atr20"] > 0


def test_build_strategy_signals_requires_ma200_for_turtle_breakout():
    panel = _panel([100 + i for i in range(25)] + [140])

    signals = build_strategy_signals(
        tickers=["005930"],
        names={"005930": "삼성전자"},
        panels={"005930": panel},
        flow_buys=[],
        short_balances={},
        fundamentals={},
    )

    assert all(s["strategy_id"] != "turtle_breakout" for s in signals)


def test_build_strategy_signals_carries_overseas_metadata():
    panel = _panel([10_000 + i * 10 for i in range(220)] + [13_000])

    signals = build_strategy_signals(
        tickers=["AAPL"],
        names={"AAPL": "Apple"},
        panels={"AAPL": panel},
        flow_buys=[],
        short_balances={},
        fundamentals={},
        metadata={
            "AAPL": {
                "asset_class": "overseas_stock",
                "market": "US",
                "exchange": "NASD",
                "quote_exchange": "NAS",
                "broker_symbol": "AAPL",
                "currency": "USD",
                "price_scale": 100,
            }
        },
    )

    assert signals
    assert all(s["asset_class"] == "overseas_stock" for s in signals)
    assert signals[0]["features"]["broker_symbol"] == "AAPL"
    assert signals[0]["price_scale"] == 100



def test_value_quality_soft_high_pbr_per_is_warning_not_block():
    # 고PBR+고PER은 소프트 — eligible 유지, warnings에만
    vq = value_quality_status({"per": 233.71, "pbr": 17.29, "roe_proxy": 7.4})
    assert vq["pass"] is True
    assert vq["status"] == "warn"
    assert "고PBR+고PER" in vq["warnings"]
    assert vq["reasons"] == []


def test_value_quality_hard_loss_blocks():
    # 적자(PER<0)는 하드 — 차단
    vq = value_quality_status({"per": -5, "pbr": 1.0, "roe_proxy": 1.0})
    assert vq["pass"] is False
    assert vq["status"] == "fail"
    assert "PER<0(적자)" in vq["reasons"]
    assert "PER<0(적자)" not in vq["warnings"]


def test_value_quality_hard_plus_soft_still_blocks():
    # 하드+소프트 동시면 차단 유지(하드 우선)
    # 하드(ROE<-5) + 소프트(고PBR+고PER) 공존 → 하드 우선 차단, 소프트는 경고로 보존
    vq = value_quality_status({"per": 100, "pbr": 20.0, "roe_proxy": -10})
    assert vq["pass"] is False
    assert "고PBR+고PER" in vq["warnings"]
    assert "ROE proxy<-5%" in vq["reasons"]


def test_value_quality_clean_pass():
    vq = value_quality_status({"per": 12, "pbr": 1.2, "roe_proxy": 10})
    assert vq["pass"] is True
    assert vq["status"] == "pass"
    assert vq["warnings"] == []
    assert vq["reasons"] == []
