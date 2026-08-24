"""장중 현금 재배치 — 30분마다 가동률을 점검해 미달분만큼 추가 진입한다.

`cashDeploy` launchd 잡은 평일 09:30~14:30 30분 간격 11회 실행되고 `guarded: true`다.
틱당 LLM 평가 후보는 `rules.cash_deploy_max_candidates_per_run`(4)건으로 제한한다.

한 회 실행 시간의 현실적 기대치는 후보당 pi 15~19초 × 4 ≈ 1분이다. 다만 이것은
보장이 아니다: `call_llm` 은 pi 실패 시 claude 로 폴백하며 **양쪽 모두에 같은
timeout(진입 180초)을 준다.** 둘 다 응답 없이 매달리는 최악의 경우 후보당 360초라
4건이면 24분으로 락 만료(15분)를 넘길 수 있다. 그때는 다음 틱이 만료된 락을
회수해 이어받으므로 스케줄이 영구히 막히지는 않지만, 겹쳐 도는 구간이 생긴다.
timeout 예산을 줄이는 것이 근본 해법이며 아직 하지 않았다.

순수 로직(compute_pending_notional / pick_candidates / should_warn_underrun)은
단위테스트하고, KIS/DB IO는 run_cash_deploy 하나에 몰아둔다
(src/orchestrator/dip_buy.py 와 같은 구조).
"""
from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

from src.orchestrator.capital import compute_capital_plan, position_eval_won
from src.orchestrator.dip_buy import _KIS_GAP_SEC, is_market_closed_reject
from src.orchestrator.entry_decision import AccountSnapshot, _asset_class, select_entries

log = logging.getLogger("stock-trader.cash_deploy")

_WARN_MARKER = Path("data/logs/.cash_deploy_underrun")


def compute_pending_notional(pending_positions) -> int:
    """PENDING 포지션의 미체결 명목금액 합. entry_price_target × entry_qty.

    D7 — 미체결 주문의 명목금액을 투자중으로 잡지 않으면, 다음 30분 틱이
    같은 현금을 다시 배치해 이중 매수가 난다.
    """
    total = 0
    for pos in pending_positions:
        price = pos.entry_price_target
        qty = pos.entry_qty
        if price is None or qty is None:
            continue
        total += price * qty
    return total


def pick_candidates(candidates: list[dict], repo: Any, limit: int) -> list[dict]:
    """국내 후보만, 보유중·당일청산 종목 제외, score 내림차순, 최대 limit 개.

    당일 청산 종목을 빼는 이유: `is_duplicate` 는 OPEN/PENDING 만 막는데,
    체결 직후 청산된 종목은 다음 틱에 깨끗해 보여 같은 종목을 반복 매수하게 된다
    (2026-08-13 실측: SK하이닉스 1주 왕복 6회, 순손익 -7,000원).
    """
    closed_today = repo.get_tickers_closed_today()
    filtered = [
        c for c in candidates
        if _asset_class(c) == "domestic_stock"
        and not repo.is_duplicate(c["ticker"])
        and c["ticker"] not in closed_today
    ]
    filtered.sort(key=lambda c: -c.get("score", 0))
    return filtered[:limit]


def fetch_live_quotes(client: Any, candidates: list[dict]) -> dict[str, Any]:
    """후보별 실시간 시세(ticker → Quote). 조회 실패 종목은 dict 에서 빠진다.

    신호 파일은 전일자라 종가가 낡았다. 그 값으로 SL/TP 를 잡고 시장가로 내면
    체결이 범위 밖에서 일어나 포지션이 태어나자마자 청산된다. 시세를 못 얻으면
    낡은 값으로 넘어가지 말고 그 후보를 건너뛴다 — 호출자가 그렇게 처리한다.
    """
    quotes: dict[str, Any] = {}
    for c in candidates:
        ticker = c["ticker"]
        try:
            quote = client.get_quote(ticker)
        except Exception as e:
            log.warning("재배치: %s 시세 조회 예외 — 건너뜀 (%s)", ticker, e)
            continue
        if not quote or quote.current_price <= 0:
            log.warning("재배치: %s 현재가 조회 실패 — 건너뜀", ticker)
            continue
        quotes[ticker] = quote
        stale = (c.get("panel_summary") or {}).get("last_close") or 0
        if stale > 0:
            drift = (quote.current_price / stale - 1) * 100
            log.info("재배치: %s 신호종가 %d → 현재가 %d (%+.2f%%)",
                     ticker, stale, quote.current_price, drift)
        time.sleep(_KIS_GAP_SEC)
    return quotes


def refresh_panel(candidate: dict, quote: Any) -> None:
    """후보의 panel_summary 를 실시간 시세로 덮어쓴다 (in-place).

    `last_close` 만 갈고 `d_change_pct` 를 두면 프롬프트가 "종가 1,612,000,
    등락률 -0.3%" 처럼 **현재가 옆에 전일 기준 등락률**을 붙여 보여준다. LLM 이
    "이 종목이 얼마나 달아올랐는가"를 잘못 읽게 되므로 둘을 함께 갈아야 한다.
    등락률은 브로커가 주는 값을 그대로 쓴다 — 우리가 재계산하지 않는다.
    """
    panel = dict(candidate.get("panel_summary") or {})
    panel["last_close"] = quote.current_price
    d_change = getattr(quote, "d_change_pct", None)
    if d_change is not None:
        panel["d_change_pct"] = d_change
    candidate["panel_summary"] = panel


def should_warn_underrun(today: str, marker: Path = _WARN_MARKER) -> bool:
    """오늘 아직 경고하지 않았으면 True. True 를 반환할 때 마커를 오늘 날짜로 갱신한다.

    읽기/쓰기 실패(OSError)는 삼키고 True 를 반환한다 — 경고를 못 보내는 것보다
    중복 경고가 낫다.
    """
    try:
        if marker.exists() and marker.read_text().strip() == today:
            return False
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(today)
        return True
    except OSError:
        return True


def _maybe_warn_underrun(plan, rules: Any, send_warning: Callable[[str], Any], detail: str) -> None:
    """detail: 미달의 실제 원인 한 줄. 2026-08-14~21 신호 봇이 빈 파일을 내는 동안
    "신호가 부족한 날이야"라는 뭉뚱그린 문구가 일주일짜리 outage를 가렸다 —
    호출 지점이 아는 원인을 그대로 내보낸다."""
    if plan.utilization_pct >= rules.cash_deploy_underrun_warn_pct:
        return
    # 기본 인자가 아니라 모듈 전역을 직접 참조 — 기본 인자는 def 시점에 바인딩돼
    # 테스트의 monkeypatch(_WARN_MARKER)가 반영되지 않는다.
    if not should_warn_underrun(date.today().isoformat(), marker=_WARN_MARKER):
        return
    send_warning(
        f"⚠️ 가동률 {plan.utilization_pct:.1f}% (목표 {plan.target_pct:.0f}%) — "
        f"배치 가능 {plan.deployable_won:,}원. {detail}"
    )


def summarize_skip_reasons(skips: list, limit: int = 3) -> str:
    """진입 0건일 때 실제 skip 사유를 한 줄로 압축한다. 같은 사유는 건수로 합친다.

    _maybe_warn_underrun 의 detail 규약과 같다 — 뭉뚱그린 문구는 outage 를 가린다.
    """
    if not skips:
        return "진입 계획이 0건인데 skip 사유도 안 나왔어 — select_entries 점검 필요."
    counts: dict[str, int] = {}
    for s in skips:
        reason = (getattr(s, "reason", None) or "사유 불명").strip() or "사유 불명"
        counts[reason] = counts.get(reason, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    shown = "; ".join(f"{reason} ({n}건)" for reason, n in ordered[:limit])
    if len(ordered) > limit:
        shown += f" 외 {len(ordered) - limit}종"
    return f"후보를 평가했지만 전부 걸러졌어 — {shown}."


def run_cash_deploy(
    client: Any,
    repo: Any,
    rules: Any,
    candidates: list[dict],
    signals: dict,
    send_info: Callable[[str], Any],
    send_warning: Callable[[str], Any],
) -> int:
    """가동률 미달분만큼 1회 재배치. 접수된 주문 수를 반환.

    예외를 밖으로 내보내지 않는다 — launchd 잡이 비정상 종료하면 안 된다.
    """
    if not rules.cash_deploy_enabled:
        log.info("cash-deploy disabled (config)")
        return 0

    balance = client.get_balance()
    if balance is None:
        send_warning("재배치: KIS 잔고 조회 실패")
        return 0

    time.sleep(_KIS_GAP_SEC)
    try:
        buying_power = client.get_deposit() or balance.cash
    except Exception as e:
        log.info("재배치: get_deposit 실패, balance.cash로 폴백 (%s)", e)
        buying_power = balance.cash

    pending_notional = compute_pending_notional(repo.get_pending_positions())
    plan = compute_capital_plan(
        total_asset_won=balance.total_eval,
        position_eval_won=position_eval_won(balance.positions),
        pending_notional_won=pending_notional,
        buying_power_won=buying_power,
        rules=rules,
    )
    log.info(
        "재배치 자본계획: total=%d invested=%d(%.1f%%) buying_power=%d deployable=%d pending=%d",
        plan.total_asset_won, plan.invested_won, plan.utilization_pct,
        plan.buying_power_won, plan.deployable_won, pending_notional,
    )

    if plan.deployable_won < rules.cash_deploy_min_deploy_won:
        _maybe_warn_underrun(plan, rules, send_warning,
                             "배치 가능액이 최소 주문 기준 미만이라 이번 틱은 쉬어가.")
        return 0

    picked = pick_candidates(candidates, repo, rules.cash_deploy_max_candidates_per_run)
    if not picked:
        if not candidates:
            _maybe_warn_underrun(plan, rules, send_warning,
                                 "국내 신호가 0건이야 — 신호 봇/KRX 데이터 소스 점검 필요.")
        else:
            _maybe_warn_underrun(plan, rules, send_warning,
                                 "후보가 전부 보유중·당일청산 제외돼서 진입할 게 없어.")
        return 0

    # 실시간 시세를 못 얻은 후보는 낡은 종가로 발주하지 않고 아예 뺀다.
    quotes = fetch_live_quotes(client, picked)
    picked = [c for c in picked if c["ticker"] in quotes]
    if not picked:
        log.warning("재배치: 시세를 얻은 후보가 없어 진입 skip")
        _maybe_warn_underrun(plan, rules, send_warning,
                             "후보 시세 조회가 전부 실패해서 진입을 건너뛰었어.")
        return 0

    # LLM 프롬프트의 시세도 실시간으로 갱신한다 — 판단 근거와 체결 가격이 어긋나면
    # 논리는 낡은 가격, 주문은 현재 가격이라는 상태가 된다.
    for c in picked:
        refresh_panel(c, quotes[c["ticker"]])
    reference_prices = {t: q.current_price for t, q in quotes.items()}

    account = AccountSnapshot(
        cash_won=balance.cash,
        total_asset_won=balance.total_eval,
        invested_won=plan.invested_won,
        pending_notional_won=pending_notional,
        open_positions=len(repo.get_active_positions()),
        daily_pnl_pct=0.0,
        daily_entries_today=repo.get_today_entries(),
    )
    plans, skips = select_entries(
        picked, signals, account, rules, repo=repo,
        deployable_won=plan.deployable_won,
        quota_override=rules.max_daily_entries + rules.cash_deploy_max_daily_entries,
        reference_prices=reference_prices,
    )
    log.info("재배치 진입 계획 %d건, skip %d건", len(plans), len(skips))
    for s in skips:
        log.info("  SKIP %s %s: %s", s.ticker, s.name, s.reason)

    # select_entries 가 전부 거른 경우도 "가동률 미달인데 아무것도 안 샀다"는 사실은
    # 같다. 이 갈래만 경고 없이 return 하고 있었고, 2026-08-24 시장 레짐 차단으로
    # 11틱(09:30~14:30) 내내 가동률 2.7%인 채 알림이 한 건도 나가지 않았다.
    if not plans:
        _maybe_warn_underrun(plan, rules, send_warning, summarize_skip_reasons(skips))
        return 0

    accepted = 0
    for p in plans:
        time.sleep(_KIS_GAP_SEC)
        result = client.submit_buy(p.ticker, p.qty, p.entry_price_tick, order_type="market")
        if not result.accepted:
            msg = str(result.raw.get("msg1", result.raw))
            if is_market_closed_reject(msg):
                log.info("재배치 %s: 정규장 시간 아님으로 skip (%s)", p.ticker, msg)
            else:
                send_warning(f"재배치 주문 거부 {p.ticker} {p.name}: {msg}")
            continue

        pos_id = repo.insert_position(
            ticker=p.ticker,
            name=p.name,
            signal_score=p.signal_score,
            confidence=int(p.decision.confidence),
            broker_order_id=result.broker_order_id,
            strategy=p.decision.entry_strategy,
            price_target=p.entry_price_tick,
            qty=p.qty,
            thesis=p.decision.key_thesis,
            watch_signals=p.decision.watch_signals,
            stop_loss=p.stop_loss_price,
            take_profit=p.take_profit_price,
            max_hold_days=p.decision.max_hold_days,
            strategy_id=p.strategy_id,
            strategy_score=p.strategy_score,
            features=p.features,
        )
        accepted += 1
        send_info(
            f"🔁 재배치 매수 접수: {p.name} ({p.ticker}) 주문번호 {result.broker_order_id}\n"
            f"포지션 ID: {pos_id}\n"
            f"전략: {p.strategy_id} (+{p.strategy_score})\n"
            f"가격 {p.entry_price_tick:,} × {p.qty}주 = {p.entry_price_tick * p.qty:,}원\n"
            f"손절 {p.stop_loss_price:,} / 익절 {p.take_profit_price:,}"
        )

    return accepted
