from __future__ import annotations

import pandas as pd

from src.signal.analysis.overseas_strategy_builder import (
    build_overseas_strategy_signals,
    overseas_panel_features,
)


def _panel(values, volume=1_000_000):
    return pd.DataFrame([
        {"시가": v - 10, "고가": v + 10, "저가": v - 20, "종가": v, "거래량": volume + i * 1000, "등락률": 0.5}
        for i, v in enumerate(values)
    ])


def test_distribution_features_create_bounded_profit_target():
    p = _panel([10_000 + i * 8 for i in range(230)] + [12_000])
    refs = {"SPY": _panel([10_000 + i * 3 for i in range(231)]), "QQQ": _panel([10_000 + i * 4 for i in range(231)]), "XLK": _panel([10_000 + i * 4 for i in range(231)]), "^VIX": _panel([18 for _ in range(231)])}

    f = overseas_panel_features("AAPL", p, refs, {"sector_etf": "XLK"})

    assert 2.0 <= f["normal_take_profit_pct"] <= 6.0
    assert 1.5 <= f["normal_stop_loss_pct"] <= 3.0
    assert "daily_return_zscore_60d" in f


def test_distribution_overextension_marks_signal_ineligible():
    p = _panel([10_000 + i * 2 for i in range(230)] + [16_000])
    refs = {"SPY": _panel([10_000 + i for i in range(231)]), "QQQ": _panel([10_000 + i for i in range(231)]), "XLK": _panel([10_000 + i for i in range(231)]), "^VIX": _panel([18 for _ in range(231)])}
    meta = {"AAPL": {"asset_class": "overseas_stock", "exchange": "NASD", "quote_exchange": "NAS", "broker_symbol": "AAPL", "currency": "USD", "price_scale": 100, "sector_etf": "XLK"}}

    signals = build_overseas_strategy_signals(["AAPL"], {"AAPL": "Apple"}, {"AAPL": p}, meta, refs)

    assert signals
    assert any(not s["eligible"] for s in signals)
    assert any("normal_distribution_overextended" in " ".join(s["filter_reasons"]) for s in signals)


# ── config-driven market_risk 게이트 (2026-06-29 추가) ──────────────────
def _mr_refs(qqq_close_seq, vix=18.0):
    """market_risk 판정용 reference panels. SPY는 보합(ret5=0)."""
    return {
        "SPY": _panel([100, 100, 100, 100, 100, 100]),
        "QQQ": _panel(qqq_close_seq),
        "XLK": _panel([100, 100, 100, 100, 100, 100]),
        "^VIX": _panel([vix, vix, vix, vix, vix, vix]),
    }


_QQQ_DOWN5 = [100, 100, 100, 100, 100, 95]   # 5일 -5% (tail6: base100 last95)
_STOCK = _panel([100 + i for i in range(40)], volume=1_000_000)


def test_market_risk_default_aggressive_does_not_trigger_on_mild_drop():
    # cfg 미지정 → 공격적 기본값(qqq -10). QQQ -5%는 트리거 안 함.
    f = overseas_panel_features("MU", _STOCK, _mr_refs(_QQQ_DOWN5), {"sector_etf": "XLK"})
    assert f["market_risk"] is False


def test_market_risk_strict_cfg_triggers_on_mild_drop():
    # 엄격 cfg(qqq -4) → QQQ -5%는 트리거.
    f = overseas_panel_features(
        "MU", _STOCK, _mr_refs(_QQQ_DOWN5), {"sector_etf": "XLK"},
        market_risk_cfg={"vix_max": 40.0, "spy_ret5_min": -8.0, "qqq_ret5_min": -4.0},
    )
    assert f["market_risk"] is True


def test_market_risk_relaxed_cfg_does_not_trigger():
    # 완화 cfg(qqq -10) → QQQ -5%는 통과.
    f = overseas_panel_features(
        "MU", _STOCK, _mr_refs(_QQQ_DOWN5), {"sector_etf": "XLK"},
        market_risk_cfg={"vix_max": 40.0, "spy_ret5_min": -8.0, "qqq_ret5_min": -10.0},
    )
    assert f["market_risk"] is False


def test_market_risk_vix_dimension_triggers():
    # 경계: VIX가 cfg vix_max 초과면 다른 지표가 멀쩡해도 트리거.
    f = overseas_panel_features(
        "MU", _STOCK, _mr_refs([100, 100, 100, 100, 100, 100], vix=30.0),
        {"sector_etf": "XLK"},
        market_risk_cfg={"vix_max": 25.0, "spy_ret5_min": -8.0, "qqq_ret5_min": -10.0},
    )
    assert f["market_risk"] is True
