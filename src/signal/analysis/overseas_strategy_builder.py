"""해외주식 전용 전략 후보 생성.

국내 수급(외국인/기관) 기반 전략과 분리한다. 해외 watchlist는 yfinance OHLCV,
상대강도(SPY/QQQ/섹터 ETF), VIX risk-off, 20/55일 돌파, 50/200일 추세를 사용한다.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Mapping

import pandas as pd

STRATEGY_VERSION = "2026-06-15.us.2"

# 정규분포/Maxwell-Boltzmann 아이디어의 안전한 적용:
# 가격을 정규분포라고 단정하지 않는다. 최근 대형·유동성 있는 미국 종목에서
# 참여자 행동이 평균/분산으로 어느 정도 수렴한다는 가정만 사용해
# 1) 과도한 +z-score 추격을 차단하고 2) 평균적인 1~1.25σ 이익폭에서 익절한다.
NORMAL_TAKE_PROFIT_SIGMA = 1.25
NORMAL_TAKE_PROFIT_MIN_PCT = 2.0
NORMAL_TAKE_PROFIT_MAX_PCT = 6.0
NORMAL_STOP_LOSS_SIGMA = 1.0
NORMAL_STOP_LOSS_MIN_PCT = 1.5
NORMAL_STOP_LOSS_MAX_PCT = 3.0
OVEREXTENDED_DAILY_RETURN_Z = 2.8
OVEREXTENDED_PRICE_Z = 2.5

# 해외 market_risk(risk-off) 게이트 기본 임계 — config(overseas_market_risk) 미지정 시 fallback.
# [공격적] 약세 구간에도 해외 후보를 살리도록 완화. 국내 market_regime OFF와 정렬.
DEFAULT_MARKET_RISK_VIX_MAX = 40.0
DEFAULT_MARKET_RISK_SPY_RET5_MIN = -8.0
DEFAULT_MARKET_RISK_QQQ_RET5_MIN = -10.0


def _to_float(v, default: float = 0.0) -> float:
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def _to_int(v, default: int = 0) -> int:
    try:
        if pd.isna(v):
            return default
        return int(round(float(v)))
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _series(panel: pd.DataFrame, col: str) -> pd.Series:
    if panel is None or panel.empty or col not in panel.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(panel[col], errors="coerce").dropna().astype(float)


def _cum_return(panel: pd.DataFrame, window: int) -> float:
    close = _series(panel, "종가")
    if len(close) < 2:
        return 0.0
    tail = close.tail(window + 1)
    if len(tail) < 2:
        return 0.0
    base = float(tail.iloc[0])
    last = float(tail.iloc[-1])
    return (last / base - 1.0) * 100.0 if base > 0 else 0.0


def _normal_distribution_features(panel: pd.DataFrame, last_close: int, atr20_pct: float) -> dict:
    """최근 수익률 분포 기반 해외장 과열/익절 feature.

    실무적으로는 Bollinger Band/통계적 차익거래의 z-score와 유사한 사용이다.
    단, 주식 수익률은 fat-tail이므로 정규분포를 맹신하지 않고 p95 절대수익률도
    같이 보며 익절/손절폭을 보수적으로 clamp한다.
    """
    close = _series(panel, "종가")
    if len(close) < 30 or last_close <= 0:
        return {
            "normal_model": "insufficient_history",
            "daily_return_mean_60d_pct": 0.0,
            "daily_return_sigma_60d_pct": 0.0,
            "daily_return_zscore_60d": 0.0,
            "price_zscore_20d": 0.0,
            "abs_return_p95_60d_pct": 0.0,
            "normal_take_profit_sigma": NORMAL_TAKE_PROFIT_SIGMA,
            "normal_take_profit_pct": NORMAL_TAKE_PROFIT_MIN_PCT,
            "normal_stop_loss_pct": NORMAL_STOP_LOSS_MIN_PCT,
            "normal_take_profit_price": int(round(last_close * (1 + NORMAL_TAKE_PROFIT_MIN_PCT / 100.0))),
            "normal_distribution_overextended": False,
        }

    returns = close.pct_change().dropna() * 100.0
    ret_tail = returns.tail(60)
    mean60 = float(ret_tail.mean()) if len(ret_tail) else 0.0
    sigma60 = float(ret_tail.std(ddof=0)) if len(ret_tail) else 0.0
    last_ret = float(returns.iloc[-1]) if len(returns) else 0.0
    ret_z = (last_ret - mean60) / sigma60 if sigma60 > 0 else 0.0
    abs_p95 = float(ret_tail.abs().quantile(0.95)) if len(ret_tail) >= 20 else 0.0

    price_tail = close.tail(20)
    price_mean = float(price_tail.mean()) if len(price_tail) else float(last_close)
    price_sigma = float(price_tail.std(ddof=0)) if len(price_tail) else 0.0
    price_z = (float(last_close) - price_mean) / price_sigma if price_sigma > 0 else 0.0

    # ATR은 고저가 range 기반이라 close-to-close σ보다 크기 쉬워 0.6만 반영.
    distribution_vol = max(sigma60, atr20_pct * 0.6)
    tp_pct = _clamp(
        max(NORMAL_TAKE_PROFIT_MIN_PCT, distribution_vol * NORMAL_TAKE_PROFIT_SIGMA),
        NORMAL_TAKE_PROFIT_MIN_PCT,
        NORMAL_TAKE_PROFIT_MAX_PCT,
    )
    sl_pct = _clamp(
        max(NORMAL_STOP_LOSS_MIN_PCT, distribution_vol * NORMAL_STOP_LOSS_SIGMA),
        NORMAL_STOP_LOSS_MIN_PCT,
        NORMAL_STOP_LOSS_MAX_PCT,
    )
    overextended = bool(
        price_z >= OVEREXTENDED_PRICE_Z
        or ret_z >= OVEREXTENDED_DAILY_RETURN_Z
        or (abs_p95 > 0 and last_ret >= abs_p95)
    )

    return {
        "normal_model": "rolling_60d_return_zscore_fat_tail_guard",
        "daily_return_mean_60d_pct": mean60,
        "daily_return_sigma_60d_pct": sigma60,
        "daily_return_zscore_60d": ret_z,
        "price_zscore_20d": price_z,
        "abs_return_p95_60d_pct": abs_p95,
        "normal_take_profit_sigma": NORMAL_TAKE_PROFIT_SIGMA,
        "normal_take_profit_pct": tp_pct,
        "normal_stop_loss_pct": sl_pct,
        "normal_take_profit_price": int(round(last_close * (1 + tp_pct / 100.0))),
        "normal_distribution_overextended": overextended,
    }


def _prev_high(panel: pd.DataFrame, window: int) -> float:
    high = _series(panel, "고가")
    if len(high) <= window:
        return 0.0
    return float(high.iloc[:-1].tail(window).max())


def _prev_low(panel: pd.DataFrame, window: int) -> float:
    low = _series(panel, "저가")
    if len(low) <= window:
        return 0.0
    return float(low.iloc[:-1].tail(window).min())


def _atr20(panel: pd.DataFrame) -> float:
    close = _series(panel, "종가")
    high = _series(panel, "고가")
    low = _series(panel, "저가")
    if len(close) < 5 or high.empty or low.empty:
        return 0.0
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1).dropna()
    return float(tr.tail(20).mean()) if len(tr) else 0.0


def _ma(panel: pd.DataFrame, window: int) -> float:
    close = _series(panel, "종가")
    if len(close) < window:
        return 0.0
    return float(close.tail(window).mean())


def _slope_pct(panel: pd.DataFrame, window: int, lag: int = 20) -> float:
    close = _series(panel, "종가")
    if len(close) < window + lag:
        return 0.0
    cur = float(close.tail(window).mean())
    prev = float(close.iloc[:-lag].tail(window).mean())
    return (cur / prev - 1.0) * 100.0 if prev > 0 else 0.0


def _earnings_days_away(meta: Mapping[str, object]) -> int | None:
    raw = meta.get("earnings_date")
    if not raw:
        return None
    try:
        d = datetime.fromisoformat(str(raw)).date()
    except ValueError:
        try:
            d = datetime.strptime(str(raw), "%Y-%m-%d").date()
        except ValueError:
            return None
    return (d - date.today()).days


def overseas_panel_features(
    ticker: str,
    panel: pd.DataFrame,
    reference_panels: Mapping[str, pd.DataFrame],
    meta: Mapping[str, object],
    market_risk_cfg: Mapping[str, float] | None = None,
) -> dict:
    close = _series(panel, "종가")
    if close.empty:
        return {}

    last_close = _to_int(close.iloc[-1])
    vol = _series(panel, "거래량")
    avg_vol_20d = float(vol.tail(20).mean()) if len(vol) >= 20 else 0.0
    last_vol = float(vol.iloc[-1]) if len(vol) else 0.0
    volume_ratio_20d = last_vol / avg_vol_20d if avg_vol_20d > 0 else 0.0

    ma50 = _ma(panel, 50)
    ma200 = _ma(panel, 200)
    ma200_slope_pct = _slope_pct(panel, 200)
    high20 = _prev_high(panel, 20)
    high55 = _prev_high(panel, 55)
    low20 = _prev_low(panel, 20)
    atr20 = _atr20(panel)
    atr20_pct = (atr20 / last_close * 100.0) if last_close > 0 else 0.0
    normal_features = _normal_distribution_features(panel, last_close, atr20_pct)

    spy = reference_panels.get("SPY", pd.DataFrame())
    qqq = reference_panels.get("QQQ", pd.DataFrame())
    sector_key = str(meta.get("sector_etf") or meta.get("benchmark_symbol") or "SPY")
    sector = reference_panels.get(sector_key, pd.DataFrame())
    vix_panel = reference_panels.get("^VIX", pd.DataFrame())

    ret5 = _cum_return(panel, 5)
    ret20 = _cum_return(panel, 20)
    ret55 = _cum_return(panel, 55)
    spy_ret5 = _cum_return(spy, 5)
    spy_ret20 = _cum_return(spy, 20)
    qqq_ret5 = _cum_return(qqq, 5)
    qqq_ret20 = _cum_return(qqq, 20)
    sector_ret20 = _cum_return(sector, 20)

    vix_close = _series(vix_panel, "종가")
    vix_level = float(vix_close.iloc[-1]) if len(vix_close) else 0.0
    vix_change_5d = _cum_return(vix_panel, 5)

    earnings_days = _earnings_days_away(meta)
    earnings_risk = earnings_days is not None and -1 <= earnings_days <= 2
    _mr = market_risk_cfg or {}
    vix_max = float(_mr.get("vix_max", DEFAULT_MARKET_RISK_VIX_MAX))
    spy_ret5_min = float(_mr.get("spy_ret5_min", DEFAULT_MARKET_RISK_SPY_RET5_MIN))
    qqq_ret5_min = float(_mr.get("qqq_ret5_min", DEFAULT_MARKET_RISK_QQQ_RET5_MIN))
    market_risk = bool(vix_level > vix_max or spy_ret5 < spy_ret5_min or qqq_ret5 < qqq_ret5_min)

    return {
        "asset_class": "overseas_stock",
        "last_close": last_close,
        "d_change_pct": _to_float(panel.iloc[-1].get("등락률", 0.0)) if not panel.empty else 0.0,
        "return_5d_pct": ret5,
        "return_20d_pct": ret20,
        "return_55d_pct": ret55,
        "volume_ratio_20d": volume_ratio_20d,
        "avg_volume_20d": avg_vol_20d,
        "ma50": ma50,
        "ma200": ma200,
        "above_ma50": bool(last_close > ma50) if ma50 > 0 else False,
        "above_ma200": bool(last_close > ma200) if ma200 > 0 else False,
        "ma50_gt_ma200": bool(ma50 > ma200) if ma50 > 0 and ma200 > 0 else False,
        "ma200_slope_pct": ma200_slope_pct,
        "donchian_high_20": high20,
        "donchian_high_55": high55,
        "donchian_low_20": low20,
        "breakout_high_20": bool(high20 > 0 and last_close > high20),
        "breakout_high_55": bool(high55 > 0 and last_close > high55),
        "atr20": atr20,
        "atr20_pct": atr20_pct,
        "turtle_stop_loss_pct": (2 * atr20 / last_close * 100.0) if last_close > 0 else 0.0,
        "turtle_take_profit_pct": (6 * atr20 / last_close * 100.0) if last_close > 0 else 0.0,
        **normal_features,
        "spy_return_5d_pct": spy_ret5,
        "spy_return_20d_pct": spy_ret20,
        "qqq_return_5d_pct": qqq_ret5,
        "qqq_return_20d_pct": qqq_ret20,
        "sector_etf": sector_key,
        "sector_return_20d_pct": sector_ret20,
        "relative_strength_spy_20d": ret20 - spy_ret20,
        "relative_strength_qqq_20d": ret20 - qqq_ret20,
        "relative_strength_sector_20d": ret20 - sector_ret20,
        "vix_level": vix_level,
        "vix_change_5d_pct": vix_change_5d,
        "market_risk": market_risk,
        "earnings_days_away": earnings_days,
        "earnings_risk": earnings_risk,
    }


def _panel_summary(features: dict) -> dict:
    return {
        "last_close": int(features.get("last_close", 0)),
        "d_change_pct": float(features.get("d_change_pct", 0.0)),
        "vol_ratio_5d": float(features.get("volume_ratio_20d", 0.0)),
        "foreign_net_5d_won": 0,
        "inst_net_5d_won": 0,
        "atr20": float(features.get("atr20", 0.0)),
        "atr20_pct": float(features.get("atr20_pct", 0.0)),
        "donchian_high_20": float(features.get("donchian_high_20", 0.0)),
        "donchian_low_10": float(features.get("donchian_low_20", 0.0)),
        "ma200": float(features.get("ma200", 0.0)),
        "ma200_slope_pct": float(features.get("ma200_slope_pct", 0.0)),
        "daily_return_zscore_60d": float(features.get("daily_return_zscore_60d", 0.0)),
        "price_zscore_20d": float(features.get("price_zscore_20d", 0.0)),
        "normal_take_profit_pct": float(features.get("normal_take_profit_pct", 0.0)),
    }


def build_overseas_strategy_signals(
    tickers: Iterable[str],
    names: Mapping[str, str],
    panels: Mapping[str, pd.DataFrame],
    metadata: Mapping[str, dict],
    reference_panels: Mapping[str, pd.DataFrame],
    market_risk_cfg: Mapping[str, float] | None = None,
) -> list[dict]:
    signals: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(ticker: str, strategy_id: str, score: int, triggers: list[str], features: dict):
        if score < 5 or (ticker, strategy_id) in seen:
            return
        seen.add((ticker, strategy_id))
        meta = dict(metadata.get(ticker, {}))
        features = dict(features)
        features.update({k: v for k, v in meta.items() if k not in features})

        filter_reasons: list[str] = []
        if features.get("market_risk"):
            filter_reasons.append("US market risk: VIX/SPY/QQQ risk-off")
        if features.get("earnings_risk"):
            filter_reasons.append("earnings event within -1~2 days")
        if features.get("normal_distribution_overextended"):
            filter_reasons.append(
                "normal_distribution_overextended: daily/price z-score already in tail"
            )
        eligible = not filter_reasons

        signal = {
            "ticker": ticker,
            "name": names.get(ticker, ticker),
            "strategy_id": strategy_id,
            "strategy_version": STRATEGY_VERSION,
            "strategy_score": int(score),
            "triggers": triggers,
            "features": features,
            "eligible": eligible,
            "filter_reasons": filter_reasons,
            "panel_summary": _panel_summary(features),
            "short_balance": None,
        }
        signal.update(meta)
        signals.append(signal)

    for ticker in tickers:
        panel = panels.get(ticker, pd.DataFrame())
        meta = metadata.get(ticker, {})
        f = overseas_panel_features(ticker, panel, reference_panels, meta, market_risk_cfg=market_risk_cfg)
        if not f:
            continue

        trend_ok = f["above_ma50"] and f["above_ma200"] and (f["ma50_gt_ma200"] or f["ma200_slope_pct"] >= 0)
        vol_ok = f["volume_ratio_20d"] >= 1.1
        atr_ok = 0.0 < f["atr20_pct"] <= 8.0
        rs_spy_ok = f["relative_strength_spy_20d"] >= 0.0
        rs_sector_ok = f["relative_strength_sector_20d"] >= 0.0

        if f["breakout_high_55"] and trend_ok and atr_ok and rs_spy_ok:
            score = 5
            if vol_ok:
                score += 1
            if f["relative_strength_spy_20d"] >= 3.0:
                score += 1
            if f["sector_return_20d_pct"] >= 0.0:
                score += 1
            add(
                ticker,
                "us_breakout_55",
                min(score, 8),
                [
                    "US:55일 고점 돌파",
                    "US:50/200일 추세 상방",
                    f"US:SPY대비 20일 상대강도 {f['relative_strength_spy_20d']:+.1f}%p",
                    f"US:분포기반 익절 목표 {f['normal_take_profit_pct']:.1f}% ({f['normal_take_profit_sigma']:.2f}σ)",
                ],
                f,
            )

        if f["breakout_high_20"] and trend_ok and atr_ok and vol_ok and rs_spy_ok:
            score = 5
            if f["relative_strength_qqq_20d"] >= 0.0:
                score += 1
            if f["return_20d_pct"] >= 8.0:
                score += 1
            add(
                ticker,
                "us_breakout_20",
                min(score, 7),
                [
                    "US:20일 고점 돌파",
                    f"US:거래량x{f['volume_ratio_20d']:.1f}",
                    f"US:VIX {f['vix_level']:.1f}",
                    f"US:분포기반 익절 목표 {f['normal_take_profit_pct']:.1f}% ({f['normal_take_profit_sigma']:.2f}σ)",
                ],
                f,
            )

        if (
            f["return_20d_pct"] >= 5.0
            and f["relative_strength_spy_20d"] >= 2.0
            and trend_ok
            and rs_sector_ok
            and f["return_5d_pct"] < 20.0
        ):
            score = 5
            if vol_ok:
                score += 1
            if f["relative_strength_sector_20d"] >= 2.0:
                score += 1
            add(
                ticker,
                "us_relative_momentum",
                min(score, 7),
                [
                    "US:SPY 대비 상대강도",
                    "US:섹터 ETF 대비 추세 확인",
                    "US:50/200일 추세 상방",
                    f"US:분포기반 익절 목표 {f['normal_take_profit_pct']:.1f}% ({f['normal_take_profit_sigma']:.2f}σ)",
                ],
                f,
            )

    signals.sort(key=lambda x: (-x["strategy_score"], x["strategy_id"], x["ticker"]))
    return signals
