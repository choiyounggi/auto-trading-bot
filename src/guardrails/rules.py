"""Trading rules — config/trading_rules.yaml 로드."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class TradingRules:
    # 가드레일
    max_size_pct: float = 5.0        # paper 검증 단계 종목당 최대 5%
    min_size_pct: float = 0.5        # BUY 최소 주문 비중
    min_stop_loss_pct: float = 1.5
    max_stop_loss_pct: float = 3.0
    min_take_profit_pct: float = 2.0
    max_take_profit_pct: float = 10.0
    max_hold_days: int = 5           # 영업일 기준
    max_position_count: int = 5
    max_daily_entries: int = 2
    min_confidence: int = 8
    entry_min_confidence: int = 8
    close_now_min_confidence: int = 8
    max_tp_raises: int = 3
    risk_per_trade_pct: float = 0.25  # 초기 손절까지 계좌/현금 기준 허용 손실률

    # 부분 익절 — TP 1차 도달 시 일부 매도로 실익 확보, 잔여분은 연장 TP로 운용
    partial_tp_enabled: bool = True
    partial_tp_sell_pct: float = 50.0       # 1차 도달 시 매도 비율 (%)
    partial_tp_extension: float = 0.5       # 잔여 TP 연장: tp + (tp-entry)×이 값
    partial_tp_breakeven_stop: bool = True  # 부분 익절 후 손절가를 본전(진입가)으로 상향

    # 해외주식 paper 예산/리스크 (가격은 USD cent 등 minor unit으로 계산)
    overseas_enabled: bool = True
    overseas_paper_capital_usd: float = 10_000.0
    overseas_risk_per_trade_pct: float = 0.25
    overseas_max_size_pct: float = 5.0

    # paper 검증용 소액 probe: 특정 전략/자산군만 confidence 문턱과 size cap을 완화
    paper_probe_enabled: bool = True
    paper_probe_min_confidence: float = 7.5
    paper_probe_max_size_pct: float = 1.0
    paper_probe_strategies: tuple[str, ...] = ("turtle_breakout",)
    paper_probe_asset_classes: tuple[str, ...] = ("overseas_stock",)

    # Kill Switch
    daily_loss_pct: float = 2.0
    weekly_loss_pct: float = 5.0
    consecutive_stop_loss: int = 5

    # 진입
    entry_default_strategy: str = "MARKET_OPEN"
    entry_signal_score_min: int = 5
    strategy_max_daily_entries: dict[str, int] = field(default_factory=lambda: {
        "flow_momentum": 1,
        "price_momentum": 1,
        "short_cover": 1,
        "turtle_breakout": 1,
    })
    strategy_max_hold_days: dict[str, int] = field(default_factory=lambda: {
        "flow_momentum": 5,
        "price_momentum": 5,
        "short_cover": 3,
        "turtle_breakout": 15,
    })

    # 모니터
    monitor_interval_min: int = 30
    trailing_atr_multiplier: float = 1.5

    # LLM
    timeout_sec_entry: int = 180
    timeout_sec_monitor: int = 90
    entry_self_consistency: int = 3
    json_parse_retries: int = 3

    # 시장 레짐 게이트 — 상향장 추격 모드. 약세장에서 BUY 차단.
    block_if_kospi_5d_pct_below: float = -2.0    # KOSPI 5일 누적 -2% 미만 → 차단
    block_if_kosdaq_5d_pct_below: float = -3.0   # KOSDAQ 5일 누적 -3% 미만 → 차단 (변동성 ↑)
    block_if_vix_above: float = 25.0             # VIX > 25 → 위험 회피 레짐
    block_if_kospi_1d_pct_below: float = -2.0    # KOSPI 당일 -2% 미만 → panic day, 익일 차단

    # 지수 ETF 단계적 줄줗 (dip-buy) — raw config dict
    dip_buy: dict = field(default_factory=dict)


def load_rules(path: Path | str = "config/trading_rules.yaml") -> TradingRules:
    p = Path(path)
    if not p.exists():
        # fallback default
        return TradingRules()

    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    g = raw.get("guardrails", {})
    ks = raw.get("kill_switch", {})
    en = raw.get("entry", {})
    sb = raw.get("strategy_budgets", {})
    mo = raw.get("monitor", {})
    llm = raw.get("llm", {})
    mr = raw.get("market_regime", {})
    ov = raw.get("overseas", {})
    pp = raw.get("paper_probe", {})
    ptp = raw.get("take_profit_partial", {})

    return TradingRules(
        max_size_pct=g.get("max_size_pct", 5.0),
        min_size_pct=g.get("min_size_pct", 0.5),
        min_stop_loss_pct=g.get("min_stop_loss_pct", 1.5),
        max_stop_loss_pct=g.get("max_stop_loss_pct", 3.0),
        min_take_profit_pct=g.get("min_take_profit_pct", 2.0),
        max_take_profit_pct=g.get("max_take_profit_pct", 10.0),
        max_hold_days=g.get("max_hold_days", 5),
        max_position_count=g.get("max_position_count", 5),
        max_daily_entries=g.get("max_daily_entries", 2),
        min_confidence=g.get("min_confidence", 8),
        entry_min_confidence=g.get("entry_min_confidence", 8),
        close_now_min_confidence=g.get("close_now_min_confidence", 8),
        max_tp_raises=g.get("max_tp_raises", 3),
        risk_per_trade_pct=g.get("risk_per_trade_pct", 0.25),
        partial_tp_enabled=ptp.get("enabled", True),
        partial_tp_sell_pct=ptp.get("sell_pct", 50.0),
        partial_tp_extension=ptp.get("extension", 0.5),
        partial_tp_breakeven_stop=ptp.get("breakeven_stop", True),
        overseas_enabled=ov.get("enabled", True),
        overseas_paper_capital_usd=ov.get("paper_capital_usd", 10_000.0),
        overseas_risk_per_trade_pct=ov.get("risk_per_trade_pct", g.get("risk_per_trade_pct", 0.25)),
        overseas_max_size_pct=ov.get("max_size_pct", g.get("max_size_pct", 5.0)),
        paper_probe_enabled=pp.get("enabled", True),
        paper_probe_min_confidence=pp.get("min_confidence", 7.5),
        paper_probe_max_size_pct=pp.get("max_size_pct", 1.0),
        paper_probe_strategies=tuple(pp.get("strategies", ["turtle_breakout"])),
        paper_probe_asset_classes=tuple(pp.get("asset_classes", ["overseas_stock"])),
        daily_loss_pct=ks.get("daily_loss_pct", 2.0),
        weekly_loss_pct=ks.get("weekly_loss_pct", 5.0),
        consecutive_stop_loss=ks.get("consecutive_stop_loss", 5),
        entry_default_strategy=en.get("default_strategy", "MARKET_OPEN"),
        entry_signal_score_min=en.get("signal_score_min", 5),
        dip_buy=(raw.get("dip_buy") or {}),
        strategy_max_daily_entries={
            name: int((cfg or {}).get("max_daily_entries", 1))
            for name, cfg in (sb or {}).items()
            if isinstance(cfg, dict) and cfg.get("enabled", True)
        } or {
            "flow_momentum": 1,
            "price_momentum": 1,
            "short_cover": 1,
            "turtle_breakout": 1,
        },
        strategy_max_hold_days={
            name: int((cfg or {}).get("max_hold_days", 5))
            for name, cfg in (sb or {}).items()
            if isinstance(cfg, dict) and cfg.get("enabled", True)
        } or {
            "flow_momentum": 5,
            "price_momentum": 5,
            "short_cover": 3,
            "turtle_breakout": 15,
        },
        monitor_interval_min=mo.get("interval_min", 30),
        trailing_atr_multiplier=mo.get("trailing_atr_multiplier", 1.5),
        timeout_sec_entry=llm.get("timeout_sec_entry", 180),
        timeout_sec_monitor=llm.get("timeout_sec_monitor", 90),
        entry_self_consistency=llm.get("entry_self_consistency", 3),
        json_parse_retries=llm.get("json_parse_retries", 3),
        block_if_kospi_5d_pct_below=mr.get("block_if_kospi_5d_pct_below", -2.0),
        block_if_kosdaq_5d_pct_below=mr.get("block_if_kosdaq_5d_pct_below", -3.0),
        block_if_vix_above=mr.get("block_if_vix_above", 25.0),
        block_if_kospi_1d_pct_below=mr.get("block_if_kospi_1d_pct_below", -2.0),
    )
