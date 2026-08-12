"""진입 결정 통합 — signal → 게이트 → LLM → clamp → 주문 사전 처리."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, replace
from datetime import date
from typing import Iterable

from src.broker.order_translator import (
    calc_qty,
    calc_risk_based_qty,
    calc_stop_loss_minor,
    calc_stop_loss_price,
    calc_take_profit_minor,
    calc_take_profit_price,
    round_to_minor_unit,
    round_to_tick,
)
from src.guardrails.clamp import clamp_entry, validate_market_regime
from src.guardrails.kill_switch import is_active as kill_active
from src.guardrails.rules import TradingRules
from src.llm.prompts import ENTRY_PROMPT_TEMPLATE
from src.llm.schemas import EntryDecision
from src.llm.self_consistency import vote_entry
from src.storage.repository import Repo, add_business_days

log = logging.getLogger(__name__)


@dataclass
class AccountSnapshot:
    cash_won: int
    open_positions: int
    daily_pnl_pct: float
    daily_entries_today: int
    cash_usd: float = 0.0
    total_asset_won: int = 0        # 0 이면 cash_won 으로 폴백
    invested_won: int = 0
    pending_notional_won: int = 0

    @property
    def sizing_base_won(self) -> int:
        """국내 사이징 기준. total_asset_won 미배선 호출자는 기존 동작(예수금)을 유지."""
        return self.total_asset_won or self.cash_won


@dataclass
class EntryPlan:
    """진입 결정 + 변환된 주문 파라미터."""
    ticker: str
    name: str
    decision: EntryDecision
    qty: int
    entry_price_tick: int
    stop_loss_price: int
    take_profit_price: int
    signal_score: int
    strategy_id: str | None = None
    strategy_score: int | None = None
    features: dict | None = None
    asset_class: str = "domestic_stock"
    exchange: str | None = None
    quote_exchange: str | None = None
    broker_symbol: str | None = None
    currency: str = "KRW"
    price_scale: int = 1


@dataclass
class SkipReason:
    ticker: str
    name: str
    reason: str


def _asset_class(candidate: dict) -> str:
    features = candidate.get("features") or {}
    return str(
        candidate.get("asset_class")
        or features.get("asset_class")
        or "domestic_stock"
    )


def _is_overseas_candidate(candidate: dict) -> bool:
    return _asset_class(candidate) == "overseas_stock"


def _candidate_meta(candidate: dict, features: dict) -> dict:
    return {
        "asset_class": _asset_class(candidate),
        "exchange": candidate.get("exchange") or features.get("exchange"),
        "quote_exchange": candidate.get("quote_exchange") or features.get("quote_exchange"),
        "broker_symbol": candidate.get("broker_symbol") or features.get("broker_symbol") or candidate.get("ticker"),
        "currency": candidate.get("currency") or features.get("currency") or "KRW",
        "price_scale": int(candidate.get("price_scale") or features.get("price_scale") or 1),
    }


def _is_paper_probe_candidate(strategy_id: str, meta: dict, rules: TradingRules) -> bool:
    if not getattr(rules, "paper_probe_enabled", True):
        return False
    return (
        strategy_id in getattr(rules, "paper_probe_strategies", ())
        or meta.get("asset_class") in getattr(rules, "paper_probe_asset_classes", ())
    )


def _effective_entry_rules(candidate: dict, features: dict, rules: TradingRules) -> tuple[TradingRules, bool, dict, str]:
    """paper_probe 후보면 confidence 문턱/size cap만 별도 완화한 rules를 반환."""
    meta = _candidate_meta(candidate, features)
    strategy_id = candidate.get("strategy_id") or "flow_momentum"
    is_probe = _is_paper_probe_candidate(strategy_id, meta, rules)
    if not is_probe:
        return rules, False, meta, strategy_id
    return replace(
        rules,
        entry_min_confidence=min(float(rules.entry_min_confidence), float(rules.paper_probe_min_confidence)),
        max_size_pct=min(float(rules.max_size_pct), float(rules.paper_probe_max_size_pct)),
    ), True, meta, strategy_id


def build_prompt(
    candidate: dict, signals: dict, account: AccountSnapshot, rules: TradingRules,
    *, deployable_won: int = 0,
) -> str:
    panel = candidate.get("panel_summary") or {}
    panel_block = (
        f"종가 {panel.get('last_close', 0):,}, 등락률 {panel.get('d_change_pct', 0):+.2f}%, "
        f"거래량 5일 평균비 {panel.get('vol_ratio_5d', 0):.2f}배, "
        f"5일 외인 순매수 {panel.get('foreign_net_5d_won', 0)/1e8:+,.0f}억, "
        f"5일 기관 순매수 {panel.get('inst_net_5d_won', 0)/1e8:+,.0f}억"
    )

    sb = candidate.get("short_balance")
    if sb:
        short_block = (
            f"잔고 비중 {sb['latest_pct']:.2f}%, 5일 {sb['pct_5d_change']:+.2f}%p, "
            f"20일 {sb['pct_20d_change']:+.2f}%p, 기준일 {sb['latest_date']}"
        )
    else:
        short_block = "데이터 없음"

    macro_lines = []
    for m in signals.get("macro", {}).get("indices", []):
        macro_lines.append(
            f"  {m['label']:<14} d {m.get('d_change_pct', 0):+5.2f}%  5d {m.get('w_change_pct', 0):+5.2f}%"
        )
    macro_block = "\n".join(macro_lines) if macro_lines else "(데이터 없음)"

    # 네이버 finance 데이터 (KOSPI/KOSDAQ 매매주체 + 시황 기사) — 있으면 보강
    naver = signals.get("naver") or {}
    if naver:
        naver_lines: list[str] = []
        for key, label in (("kospi_flow", "KOSPI"), ("kosdaq_flow", "KOSDAQ")):
            flow = naver.get(key) or {}
            flows = flow.get("flows") or []
            if flows:
                naver_lines.append(f"  [{label} 매매주체 일별]")
                for f in flows[:5]:
                    naver_lines.append(
                        f"    {f.get('date',''):<12} "
                        f"개인 {f.get('individual_net_won', 0)/1e8:>+8,.0f}억  "
                        f"외인 {f.get('foreign_net_won', 0)/1e8:>+8,.0f}억  "
                        f"기관 {f.get('institution_net_won', 0)/1e8:>+8,.0f}억"
                    )
        if naver_lines:
            macro_block += "\n\n[네이버 finance 시장 매매주체]\n" + "\n".join(naver_lines)

    news_lines = []
    for n in signals.get("macro", {}).get("headlines", [])[:5]:
        news_lines.append(f"  - {n.get('title', '')}")
    naver_headlines = naver.get("headlines") or []
    if naver_headlines:
        news_lines.append("\n[네이버 시황 기사 — 시장 큐레이션]")
        for h in naver_headlines[:8]:
            code = h.get("code", "")
            title = h.get("title", "")
            news_lines.append(f"  - [{code}] {title}")
    news_block = "\n".join(news_lines) or "(헤드라인 없음)"

    prior = candidate.get("llm_analysis", {}) or {}
    prior_text = (prior.get("text") or "")[:1500]

    strategy_id = candidate.get("strategy_id") or "flow_momentum"
    strategy_score = candidate.get("strategy_score") or candidate.get("score", 0)
    features = candidate.get("features") or {}
    feature_lines = [f"  - {k}: {v}" for k, v in features.items() if k != "value_quality"][:12]
    value_quality = features.get("value_quality") or {}
    strategy_block = (
        f"전략 ID: {strategy_id}\n"
        f"전략 점수: {strategy_score}/10\n"
        f"전략 features:\n" + ("\n".join(feature_lines) if feature_lines else "  (없음)") + "\n"
        f"value_quality: {value_quality}"
    )

    return ENTRY_PROMPT_TEMPLATE.format(
        ticker=candidate["ticker"],
        name=candidate["name"],
        score=candidate["score"],
        triggers=", ".join(candidate.get("triggers", [])),
        strategy_block=strategy_block,
        prior_llm_analysis=prior_text or "(없음)",
        panel_block=panel_block,
        short_block=short_block,
        macro_block=macro_block,
        news_block=news_block,
        total_asset_won=account.sizing_base_won,
        invested_won=account.invested_won,
        utilization_pct=(account.invested_won / account.sizing_base_won * 100) if account.sizing_base_won else 0.0,
        target_utilization_pct=rules.target_utilization_pct,
        deployable_won=deployable_won,
        open_positions=account.open_positions,
        max_positions=rules.max_position_count,
        daily_pnl_pct=account.daily_pnl_pct,
    )


def _candidate_price_at_decision(candidate: dict, decision: EntryDecision) -> int:
    panel = candidate.get("panel_summary") or {}
    price = int(panel.get("last_close", 0) or 0)
    if price <= 0:
        price = int(decision.entry_price or 0)
    return price


def _log_entry_decision(
    repo: Repo | None,
    candidate: dict,
    prompt: str,
    decision: EntryDecision,
    trace: list[dict],
) -> None:
    """ENTRY LLM 판단을 사후평가 가능하게 영구 저장."""
    if repo is None:
        return
    today = date.today()
    parse_errors = [t.get("parse_error") for t in trace if t.get("parse_error")]
    response_text = json.dumps(trace, ensure_ascii=False)
    response_json = decision.model_dump_json()
    repo.log_llm_decision(
        position_id=None,
        decision_type="ENTRY",
        model="claude-sonnet",
        source=trace[0].get("source", "unavailable") if trace else "unavailable",
        prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        ticker=candidate.get("ticker"),
        name=candidate.get("name"),
        signal_score=int(candidate.get("score", 0) or 0),
        strategy_id=candidate.get("strategy_id") or "flow_momentum",
        strategy_score=int(candidate.get("strategy_score", candidate.get("score", 0)) or 0),
        features_json=json.dumps(candidate.get("features") or {}, ensure_ascii=False),
        decision_date=today,
        eval_due_date=add_business_days(today, 5),
        price_at_decision=_candidate_price_at_decision(candidate, decision),
        response_text=response_text,
        response_json=response_json,
        confidence=int(decision.confidence or 0),
        action=decision.action,
        elapsed_ms=sum(int(t.get("elapsed_ms", 0) or 0) for t in trace),
        parse_error="; ".join(str(e) for e in parse_errors) if parse_errors else None,
    )


def evaluate_candidate(
    candidate: dict,
    signals: dict,
    account: AccountSnapshot,
    rules: TradingRules,
    repo: Repo | None = None,
    *,
    budget_won: int | None = None,
) -> tuple[EntryPlan | None, str | None]:
    """단일 후보 평가. (EntryPlan, None) 또는 (None, skip_reason)."""

    # 1. 시그널 점수 임계
    if candidate.get("score", 0) < rules.entry_signal_score_min:
        return None, f"signal score {candidate.get('score')} < {rules.entry_signal_score_min}"

    # 2. LLM 결정 (self-consistency 3회)
    prompt = build_prompt(candidate, signals, account, rules, deployable_won=budget_won or 0)
    decision, trace = vote_entry(prompt, n=rules.entry_self_consistency, timeout=rules.timeout_sec_entry)

    if decision.action == "SKIP":
        _log_entry_decision(repo, candidate, prompt, decision, trace)
        return None, f"LLM SKIP (parse_errors: {sum(1 for t in trace if t['parse_error'])})"

    # 3. 가드레일 clamp
    # paper_probe 후보(turtle_breakout/overseas_stock)는 거래 샘플 확보를 위해
    # confidence 문턱과 size cap만 별도로 완화한다. 리스크 예산은 그대로 유지.
    features = candidate.get("features") or {}
    effective_rules, is_probe, meta, strategy_id = _effective_entry_rules(candidate, features, rules)
    clamped = clamp_entry(decision, effective_rules)
    if clamped.action == "SKIP":
        _log_entry_decision(repo, candidate, prompt, clamped, trace)
        suffix = " (paper_probe)" if is_probe else ""
        return None, f"confidence {decision.confidence} < {effective_rules.entry_min_confidence}{suffix}"

    # 4. 전략별 deterministic 리스크 보정
    if meta["asset_class"] == "overseas_stock" and not rules.overseas_enabled:
        _log_entry_decision(repo, candidate, prompt, clamped, trace)
        return None, "overseas_disabled"

    stop_loss_pct = float(clamped.stop_loss_pct or rules.min_stop_loss_pct)
    take_profit_pct = float(clamped.take_profit_pct or rules.min_take_profit_pct)

    if strategy_id in {"turtle_breakout", "us_breakout_20", "us_breakout_55"}:
        # Turtle/US breakout 전략은 LLM 감이 아니라 ATR20×2 손절폭을 기준으로 하되, 운영 cap은 유지한다.
        turtle_stop = float(features.get("turtle_stop_loss_pct") or 0.0)
        turtle_tp = float(features.get("turtle_take_profit_pct") or 0.0)
        if turtle_stop > 0:
            stop_loss_pct = max(min(turtle_stop, rules.max_stop_loss_pct), rules.min_stop_loss_pct)
        if turtle_tp > 0:
            take_profit_pct = max(
                min(max(turtle_tp, stop_loss_pct * 3), rules.max_take_profit_pct),
                rules.min_take_profit_pct,
            )

    if meta["asset_class"] == "overseas_stock":
        # 해외장은 정규분포를 맹신하지 않고, 최근 60일 수익률 σ + p95 tail guard로
        # 산출한 보수적 익절폭을 우선한다. 목적은 "평균적인 분포 이득"을 먹고
        # 오래 끌지 않는 paper-probe 성격의 청산이다.
        normal_stop = float(features.get("normal_stop_loss_pct") or 0.0)
        normal_tp = float(features.get("normal_take_profit_pct") or 0.0)
        if normal_stop > 0:
            stop_loss_pct = max(min(normal_stop, rules.max_stop_loss_pct), rules.min_stop_loss_pct)
        if normal_tp > 0:
            take_profit_pct = max(min(normal_tp, rules.max_take_profit_pct), rules.min_take_profit_pct)

    strategy_hold_days = int(getattr(rules, "strategy_max_hold_days", {}).get(strategy_id, clamped.max_hold_days or rules.max_hold_days))
    clamped = clamped.model_copy(update={
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
        "max_hold_days": strategy_hold_days,
    })

    _log_entry_decision(repo, candidate, prompt, clamped, trace)

    # 5. 가격/수량 보정 — 포지션 비중 cap과 계좌 리스크 cap 중 더 작은 수량
    if meta["asset_class"] == "overseas_stock":
        # 해외 신호는 stock-signal-bot이 last_close를 minor unit(USD cent)로 넣는다.
        # LLM이 달러 단위 entry_price를 반환해도 deterministic 신호 가격을 우선한다.
        scale = max(int(meta["price_scale"] or 100), 1)
        raw_entry = features.get("last_close") or clamped.entry_price
        entry_price = round_to_minor_unit(raw_entry, mode="floor", scale=scale)
        stop_loss = calc_stop_loss_minor(entry_price, clamped.stop_loss_pct)
        take_profit = calc_take_profit_minor(entry_price, clamped.take_profit_pct)
        capital = int(float(account.cash_usd or rules.overseas_paper_capital_usd) * scale)
        size_pct = min(float(clamped.size_pct or 0.0), float(rules.overseas_max_size_pct))
        risk_pct = float(rules.overseas_risk_per_trade_pct)
        clamped = clamped.model_copy(update={"entry_price": entry_price, "size_pct": size_pct})
    else:
        scale = 1
        entry_price = round_to_tick(clamped.entry_price, mode="floor")  # 매수 보수
        stop_loss = calc_stop_loss_price(entry_price, clamped.stop_loss_pct)
        take_profit = calc_take_profit_price(entry_price, clamped.take_profit_pct)
        capital = account.sizing_base_won      # ← account.cash_won 에서 변경
        size_pct = float(clamped.size_pct or 0.0)
        risk_pct = float(rules.risk_per_trade_pct)

    size_qty = calc_qty(entry_price, capital, size_pct)
    risk_qty = calc_risk_based_qty(entry_price, stop_loss, capital, risk_pct)
    # paper_probe는 거래 샘플 확보가 목적이므로 size cap으로 0주가 되더라도,
    # 1주 매수 가능하고 초기 손절 리스크가 예산 안이면 최소 1주만 허용한다.
    if is_probe and size_qty <= 0 and risk_qty >= 1 and entry_price <= capital:
        size_qty = 1
    qty = min(size_qty, risk_qty) if risk_qty > 0 else size_qty
    if budget_won is not None:
        budget_qty = budget_won // entry_price if entry_price > 0 else 0
        qty = min(qty, budget_qty)
    if qty <= 0:
        return None, (
            f"qty=0 (price={entry_price}, capital={capital}, "
            f"size_pct={size_pct}, risk_per_trade_pct={risk_pct}, asset_class={meta['asset_class']}, "
            f"budget_won={budget_won})"
        )

    return EntryPlan(
        ticker=candidate["ticker"],
        name=candidate["name"],
        decision=clamped,
        qty=qty,
        entry_price_tick=entry_price,
        stop_loss_price=stop_loss,
        take_profit_price=take_profit,
        signal_score=candidate["score"],
        strategy_id=candidate.get("strategy_id") or "flow_momentum",
        strategy_score=int(candidate.get("strategy_score", candidate.get("score", 0)) or 0),
        features=candidate.get("features") or {},
        asset_class=meta["asset_class"],
        exchange=meta["exchange"],
        quote_exchange=meta["quote_exchange"],
        broker_symbol=meta["broker_symbol"],
        currency=meta["currency"],
        price_scale=scale,
    ), None


def select_entries(
    candidates: Iterable[dict],
    signals: dict,
    account: AccountSnapshot,
    rules: TradingRules,
    kill_switch_file: str | None = None,
    repo: Repo | None = None,
    *,
    deployable_won: int | None = None,
    quota_override: int | None = None,
) -> tuple[list[EntryPlan], list[SkipReason]]:
    """전체 후보 → 진입 계획 + 거부 사유."""
    candidates = list(candidates)

    # 0. Kill Switch
    if kill_active(kill_switch_file or "data/KILL_SWITCH"):
        log.warning("Kill Switch 활성 → 전종목 SKIP")
        return [], [SkipReason(c["ticker"], c["name"], "kill_switch_active") for c in candidates]

    # 1. 시장 레짐 게이트
    # 국내 KRX 레짐 차단은 국내 후보에만 적용한다. 해외 후보는 VIX 규칙만 참고하도록
    # LLM prompt에 전달하고, 여기서 한국 지수 하락만으로 일괄 차단하지 않는다.
    initial_skips: list[SkipReason] = []
    macro = signals.get("macro", {}).get("indices", [])
    ok, reason = validate_market_regime(macro, rules)
    if not ok:
        log.warning("시장 레짐 차단(국내 후보): %s", reason)
        remaining = []
        for c in candidates:
            if _is_overseas_candidate(c):
                remaining.append(c)
            else:
                initial_skips.append(SkipReason(c["ticker"], c["name"], f"market_regime: {reason}"))
        candidates = remaining
        if not candidates:
            return [], initial_skips

    # 2. 일일 진입 한도
    if account.daily_entries_today >= rules.max_daily_entries:
        return [], [SkipReason(c["ticker"], c["name"], "daily_entry_limit") for c in candidates]

    # 3. 보유 종목 한도
    if account.open_positions >= rules.max_position_count:
        return [], [SkipReason(c["ticker"], c["name"], "position_count_limit") for c in candidates]

    # 4. 후보별 LLM 평가
    plans: list[EntryPlan] = []
    skips: list[SkipReason] = list(initial_skips)
    quota = (quota_override if quota_override is not None else rules.max_daily_entries) - account.daily_entries_today

    selected_tickers: set[str] = set()
    strategy_counts: dict[str, int] = {}
    per_strategy_limit = getattr(rules, "strategy_max_daily_entries", {}) or {}
    remaining = deployable_won

    for c in sorted(candidates, key=lambda x: -x.get("score", 0)):
        strategy_id = c.get("strategy_id") or "flow_momentum"
        is_overseas = _is_overseas_candidate(c)
        if len(plans) >= quota:
            skips.append(SkipReason(c["ticker"], c["name"], "quota_exhausted"))
            continue
        if c["ticker"] in selected_tickers:
            skips.append(SkipReason(c["ticker"], c["name"], "ticker_already_selected_today"))
            continue
        if strategy_counts.get(strategy_id, 0) >= int(per_strategy_limit.get(strategy_id, 1)):
            skips.append(SkipReason(c["ticker"], c["name"], f"strategy_daily_limit:{strategy_id}"))
            continue
        # 해외 후보에는 원화 배치 예산을 적용하지 않는다 (원화 예산 ≠ USD 예산)
        if not is_overseas and remaining is not None and remaining <= 0:
            skips.append(SkipReason(c["ticker"], c["name"], "budget_exhausted"))
            continue
        candidate_budget = None if is_overseas else remaining
        plan, reason = evaluate_candidate(c, signals, account, rules, repo=repo, budget_won=candidate_budget)
        if plan:
            plans.append(plan)
            selected_tickers.add(plan.ticker)
            strategy_counts[strategy_id] = strategy_counts.get(strategy_id, 0) + 1
            if not is_overseas and remaining is not None:
                remaining -= plan.entry_price_tick * plan.qty
        else:
            skips.append(SkipReason(c["ticker"], c["name"], reason or "unknown"))

    return plans, skips
