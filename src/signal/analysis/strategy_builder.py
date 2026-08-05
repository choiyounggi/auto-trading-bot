"""다중 전략 후보 생성.

전략 구성:
- flow_momentum: 기존 기관/외국인 수급 BUY 신호
- price_momentum: 가격/거래량 추세
- short_cover: 공매도 잔고 감소 + 수급/가격 확인
- turtle_breakout: 20일 고점 돌파 + 200일선 필터 + ATR20 리스크 모델
- value_quality: 단독 매수 신호가 아니라 filter-only feature
"""
from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from datetime import date
from typing import Iterable, Mapping

import pandas as pd

log = logging.getLogger(__name__)


STRATEGY_VERSION = "2026-06-15.1"


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
        return int(float(v))
    except Exception:
        return default


def _asdict_safe(obj):
    if obj is None:
        return None
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return obj


def _cum_return(panel: pd.DataFrame, window: int) -> float:
    if panel is None or panel.empty or "종가" not in panel.columns:
        return 0.0
    tail = panel.tail(window + 1)
    if len(tail) < 2:
        return 0.0
    base = _to_float(tail["종가"].iloc[0])
    last = _to_float(tail["종가"].iloc[-1])
    if base <= 0:
        return 0.0
    return (last / base - 1) * 100


def _price_series(panel: pd.DataFrame, col: str, fallback: pd.Series) -> pd.Series:
    if col in panel.columns:
        return panel[col].astype(float)
    return fallback.astype(float)


def _atr20(panel: pd.DataFrame) -> float:
    """20일 Average True Range. 고가/저가가 없으면 종가 기반 0에 가까운 보수 fallback."""
    if panel is None or panel.empty or "종가" not in panel.columns:
        return 0.0
    close = panel["종가"].astype(float)
    high = _price_series(panel, "고가", close)
    low = _price_series(panel, "저가", close)
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1).dropna()
    if len(tr) < 5:
        return 0.0
    return float(tr.tail(20).mean())


def panel_features(panel: pd.DataFrame) -> dict:
    if panel is None or panel.empty or "종가" not in panel.columns:
        return {}

    last = panel.iloc[-1]
    close_series = panel["종가"].astype(float)
    high_series = _price_series(panel, "고가", close_series)
    low_series = _price_series(panel, "저가", close_series)
    last_close = _to_int(last.get("종가", 0))

    avg_vol_20d = _to_float(panel["거래량"].tail(20).mean()) if "거래량" in panel.columns else 0.0
    last_vol = _to_float(last.get("거래량", 0))
    volume_ratio = last_vol / avg_vol_20d if avg_vol_20d > 0 else 0.0

    ma20 = _to_float(close_series.tail(20).mean()) if len(close_series) >= 20 else 0.0
    ma200 = _to_float(close_series.tail(200).mean()) if len(close_series) >= 200 else 0.0
    prev_ma200 = _to_float(close_series.iloc[:-20].tail(200).mean()) if len(close_series) >= 220 else ma200
    ma200_slope_pct = ((ma200 / prev_ma200 - 1) * 100) if prev_ma200 > 0 else 0.0

    prev_high20 = high_series.iloc[:-1].tail(20)
    prev_low10 = low_series.iloc[:-1].tail(10)
    donchian_high_20 = _to_float(prev_high20.max()) if len(prev_high20) >= 20 else 0.0
    donchian_low_10 = _to_float(prev_low10.min()) if len(prev_low10) >= 10 else 0.0

    atr20 = _atr20(panel)
    atr20_pct = (atr20 / last_close * 100) if last_close > 0 else 0.0

    last5 = panel.tail(5)
    foreign5 = int(last5["foreign_net"].sum()) if "foreign_net" in panel.columns else 0
    inst5 = int(last5["inst_net"].sum()) if "inst_net" in panel.columns else 0

    turtle_stop_loss_pct = (2 * atr20 / last_close * 100) if last_close > 0 else 0.0
    turtle_take_profit_pct = (6 * atr20 / last_close * 100) if last_close > 0 else 0.0

    return {
        "last_close": last_close,
        "d_change_pct": _to_float(last.get("등락률", 0.0)),
        "return_5d_pct": _cum_return(panel, 5),
        "return_20d_pct": _cum_return(panel, min(20, max(len(panel) - 1, 1))),
        "volume_ratio_20d": volume_ratio,
        "avg_volume_20d": avg_vol_20d,
        "above_ma20": bool(last_close > ma20) if ma20 > 0 else False,
        "ma20": ma20,
        "ma200": ma200,
        "ma200_available": bool(ma200 > 0),
        "above_ma200": bool(last_close > ma200) if ma200 > 0 else False,
        "ma200_slope_pct": ma200_slope_pct,
        "donchian_high_20": donchian_high_20,
        "donchian_low_10": donchian_low_10,
        "breakout_high_20": bool(donchian_high_20 > 0 and last_close > donchian_high_20),
        "atr20": atr20,
        "atr20_pct": atr20_pct,
        "turtle_stop_loss_pct": turtle_stop_loss_pct,
        "turtle_take_profit_pct": turtle_take_profit_pct,
        "foreign_net_5d_won": foreign5,
        "inst_net_5d_won": inst5,
        "smart_money_5d_won": foreign5 + inst5,
    }


def fetch_fundamentals(ref_date: date) -> dict[str, dict]:
    """PER/PBR 등 기본 펀더멘털. 실패 시 빈 dict."""
    try:
        from pykrx import stock
    except Exception as e:
        log.warning("pykrx import 실패(fundamentals): %s", e)
        return {}

    date_s = ref_date.strftime("%Y%m%d")
    frames = []
    for market in ("ALL", "KOSPI", "KOSDAQ"):
        try:
            df = stock.get_market_fundamental_by_ticker(date_s, market=market)
            if df is not None and not df.empty:
                frames.append(df)
                if market == "ALL":
                    break
        except Exception as e:
            log.info("fundamental fetch 실패 market=%s: %s", market, e)
    if not frames:
        return {}

    df = pd.concat(frames)
    out: dict[str, dict] = {}
    for ticker, row in df.iterrows():
        eps = _to_float(row.get("EPS", 0))
        bps = _to_float(row.get("BPS", 0))
        roe_proxy = (eps / bps * 100) if bps > 0 else 0.0
        out[str(ticker)] = {
            "per": _to_float(row.get("PER", 0)),
            "pbr": _to_float(row.get("PBR", 0)),
            "eps": eps,
            "bps": bps,
            "div": _to_float(row.get("DIV", 0)),
            "dps": _to_float(row.get("DPS", 0)),
            "roe_proxy": roe_proxy,
        }
    return out


# 소프트(비차단) 밸류 경고 — eligible을 막지 않고 경고만 표시. 나머지는 하드(차단).
SOFT_VALUE_REASONS = {"고PBR+고PER"}


def value_quality_status(fundamental: dict | None) -> dict:
    if not fundamental:
        return {"status": "unknown", "pass": True, "reasons": [], "warnings": [],
                "per": 0.0, "pbr": 0.0, "roe_proxy": 0.0}

    per = _to_float(fundamental.get("per", 0))
    pbr = _to_float(fundamental.get("pbr", 0))
    roe = _to_float(fundamental.get("roe_proxy", 0))
    reasons: list[str] = []

    if per < 0:
        reasons.append("PER<0(적자)")
    if pbr <= 0:
        reasons.append("PBR<=0")
    if pbr > 15 and per > 80:
        reasons.append("고PBR+고PER")
    if roe < -5:
        reasons.append("ROE proxy<-5%")

    hard = [r for r in reasons if r not in SOFT_VALUE_REASONS]
    soft = [r for r in reasons if r in SOFT_VALUE_REASONS]

    return {
        "status": "fail" if hard else ("warn" if soft else "pass"),
        "pass": not hard,          # eligible은 하드 사유로만 차단
        "reasons": hard,           # 차단 사유(하드)만
        "warnings": soft,          # 비차단 경고(소프트)
        "per": per,
        "pbr": pbr,
        "roe_proxy": roe,
    }


def build_short_balance_pool(panels: dict[str, pd.DataFrame]) -> list[str]:
    """공매도 전략 후보가 될 만한 종목만 선별해 KRX 호출 수를 제한."""
    pool: list[str] = []
    for ticker, panel in panels.items():
        f = panel_features(panel)
        if not f:
            continue
        if (
            f["return_5d_pct"] > 0
            and f["volume_ratio_20d"] >= 1.1
            and f["smart_money_5d_won"] > 0
        ):
            pool.append(ticker)
    return pool[:15]


def build_strategy_signals(
    tickers: Iterable[str],
    names: dict[str, str],
    panels: dict[str, pd.DataFrame],
    flow_buys: list,
    short_balances: dict,
    fundamentals: dict[str, dict],
    metadata: Mapping[str, dict] | None = None,
) -> list[dict]:
    signals: list[dict] = []
    seen: set[tuple[str, str]] = set()
    flow_by_ticker = {s.ticker: s for s in flow_buys}
    metadata = metadata or {}

    def add(ticker: str, strategy_id: str, score: int, triggers: list[str], features: dict):
        key = (ticker, strategy_id)
        if key in seen or score < 5:
            return
        seen.add(key)
        fundamental = fundamentals.get(ticker)
        vq = value_quality_status(fundamental)
        features = dict(features)
        meta = dict(metadata.get(ticker, {}))
        features.update({k: v for k, v in meta.items() if k not in features})
        features["value_quality"] = vq
        signal = {
            "ticker": ticker,
            "name": names.get(ticker, ticker),
            "strategy_id": strategy_id,
            "strategy_version": STRATEGY_VERSION,
            "strategy_score": int(score),
            "triggers": triggers,
            "features": features,
            "eligible": bool(vq.get("pass", True)),
            "filter_reasons": [] if vq.get("pass", True) else list(vq.get("reasons", [])),
            "value_warnings": list(vq.get("warnings", [])),
            "panel_summary": {
                "last_close": int(features.get("last_close", 0)),
                "d_change_pct": float(features.get("d_change_pct", 0.0)),
                "vol_ratio_5d": float(features.get("volume_ratio_20d", 0.0)),
                "foreign_net_5d_won": int(features.get("foreign_net_5d_won", 0)),
                "inst_net_5d_won": int(features.get("inst_net_5d_won", 0)),
                "atr20": float(features.get("atr20", 0.0)),
                "atr20_pct": float(features.get("atr20_pct", 0.0)),
                "donchian_high_20": float(features.get("donchian_high_20", 0.0)),
                "donchian_low_10": float(features.get("donchian_low_10", 0.0)),
                "ma200": float(features.get("ma200", 0.0)),
                "ma200_slope_pct": float(features.get("ma200_slope_pct", 0.0)),
            },
            "short_balance": _asdict_safe(short_balances.get(ticker)),
        }
        signal.update(meta)
        signals.append(signal)

    for ticker in tickers:
        panel = panels.get(ticker, pd.DataFrame())
        f = panel_features(panel)
        if not f:
            continue

        flow = flow_by_ticker.get(ticker)
        if flow is not None:
            add(
                ticker,
                "flow_momentum",
                int(flow.score),
                list(flow.triggers),
                f,
            )

        if (
            f["return_20d_pct"] >= 5.0
            and 0.0 <= f["return_5d_pct"] < 15.0
            and f["volume_ratio_20d"] >= 1.2
            and f["above_ma20"]
        ):
            score = 5
            if f["return_20d_pct"] >= 10.0:
                score += 1
            if f["volume_ratio_20d"] >= 1.5:
                score += 1
            if f["smart_money_5d_won"] > 0:
                score += 1
            add(
                ticker,
                "price_momentum",
                min(score, 8),
                [
                    "PM:20일 상승추세",
                    "PM:20일선 상회",
                    f"PM:거래량x{f['volume_ratio_20d']:.1f}",
                ],
                f,
            )

        if (
            f["breakout_high_20"]
            and f["above_ma200"]
            and f["ma200_slope_pct"] >= -0.5
            and 0.0 < f["atr20_pct"] <= 8.0
            and f["return_5d_pct"] < 20.0
        ):
            features = dict(f)
            features["risk_model"] = "atr20_2x_account_risk"
            features["initial_stop_price_raw"] = f["last_close"] - 2 * f["atr20"]
            score = 5
            if f["volume_ratio_20d"] >= 1.2:
                score += 1
            if f["smart_money_5d_won"] > 0:
                score += 1
            if f["ma200_slope_pct"] > 0:
                score += 1
            add(
                ticker,
                "turtle_breakout",
                min(score, 8),
                [
                    "TB:20일 고점 종가 돌파",
                    "TB:200일선 상회",
                    f"TB:ATR20 {f['atr20_pct']:.1f}%",
                ],
                features,
            )

        sb = short_balances.get(ticker)
        if sb and (
            sb.latest_pct >= 1.0
            and sb.pct_5d_change <= -0.10
            and f["return_5d_pct"] > 0
            and (f["smart_money_5d_won"] > 0 or f["volume_ratio_20d"] >= 1.2)
        ):
            score = 5
            if sb.latest_pct >= 3.0:
                score += 1
            if sb.pct_5d_change <= -0.30:
                score += 1
            if f["volume_ratio_20d"] >= 1.5:
                score += 1
            features = dict(f)
            features["short_balance"] = _asdict_safe(sb)
            add(
                ticker,
                "short_cover",
                min(score, 8),
                [
                    f"SC:공매도잔고 {sb.latest_pct:.2f}%",
                    f"SC:5일 잔고 {sb.pct_5d_change:+.2f}%p",
                    "SC:가격/수급 확인",
                ],
                features,
            )

    signals.sort(key=lambda x: (-x["strategy_score"], x["strategy_id"], x["ticker"]))
    return signals
