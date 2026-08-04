"""trade-orchestrator entrypoint — 평일 16:35 launchd fire.

흐름:
  1. 환경 검증 (Kill Switch off, DB 접근, signal JSON)
  2. signal JSON 로드 + 필터
  3. 계좌 스냅샷 + 보유 종목 수
  4. 시장 레짐 게이트 + LLM 진입 결정 (orchestrator.entry_decision.select_entries)
  5. 주문 (KIS REST API)
  6. positions DB INSERT
  7. Telegram 알림
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.broker.kis_client import KisClient
from src.guardrails.rules import load_rules
from src.notify.telegram import send_critical, send_info, send_warning
from src.orchestrator.entry_decision import AccountSnapshot, select_entries
from src.orchestrator.dip_buy import run_dip_buy
from src.orchestrator.signal_loader import filter_buy_candidates, latest_signal_date, load_signal
from src.storage.repository import Repo
from src.util.keychain import load_kis_keys, load_telegram_keys

# Keychain → os.environ inject (launchd context에선 GUI session keychain access)
load_kis_keys()
load_telegram_keys()

log = logging.getLogger("stock-trader.orchestrator")
Path("data/logs").mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("data/logs/orchestrator.log"),
        logging.StreamHandler(sys.stdout),
    ],
)


def _asset_class(candidate: dict) -> str:
    features = candidate.get("features") or {}
    return str(candidate.get("asset_class") or features.get("asset_class") or "domestic_stock")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--asset-class",
        choices=["all", "domestic_stock", "overseas_stock"],
        default="all",
        help="진입 평가 대상 자산군 필터. 미국장 job은 overseas_stock만 사용.",
    )
    p.add_argument("--signal-max-age-min", type=int, default=60)
    p.add_argument("--dip-only", action="store_true",
                   help="장중 지수 ETF dip-buy만 실행하고 종료 (15:00 전용 잡).")
    p.add_argument("--carry-over", action="store_true",
                   help="전일(최신) 신호로 익일 시가(시장가) 진입. tradeorch 09:05 잡 전용.")
    return p.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    rules = load_rules(Path("config/trading_rules.yaml"))

    # 지수 ETF 단계적 줄줗(dip-buy) 전용 모드 — 장중(예: 15:00) 잡에서 --dip-only로 호출.
    # paper는 정규장(09:00~15:30)에만 체결되므로 16:45 본잡이 아니라 장중에 돌린다.
    if args.dip_only:
        if (rules.dip_buy or {}).get("enabled"):
            try:
                with KisClient(mode="paper").session() as _dc:
                    _n = run_dip_buy(_dc, Repo(), rules, send_info, send_warning)
                    log.info("dip-buy(dip-only): %d개 단계 매수", _n)
            except Exception as e:
                log.warning("dip-buy 실패: %s", e)
        else:
            log.info("dip-buy disabled (config)")
        return 0

    # 1. signal JSON 로드 (생성 race 대비 30초 간격 3회 재시도)
    import time as _time
    from datetime import date as _date

    signal_dir = Path.home() / "stock-signal-bot" / "data" / "signals"
    signal_suffix = ".us" if args.asset_class == "overseas_stock" else ""

    # carry-over: 오늘 신호는 16:30에야 생성되므로, 09:05 잡은 전일(최신) 신호로 진입한다.
    target_date = None
    max_age_min = args.signal_max_age_min
    if args.carry_over:
        target_date = latest_signal_date(signal_dir, signal_suffix)
        if target_date is None:
            log.warning("carry-over: 사용 가능한 signal 파일 없음 (%s)", signal_dir)
            send_warning("carry-over signal 파일 없음 — 진입 skip")
            return 0
        max_age_min = 10080  # 7일: 주말·연휴 갭 커버 (최신 파일만 고르므로 신선도 사실상 무력)
        age_days = (_date.today() - target_date).days
        log.info("carry-over: 전일 신호 사용 date=%s (%d일 전)", target_date, age_days)
        if age_days > 4:
            send_warning(f"carry-over 신호가 {age_days}일 전 — signal-bot 점검 필요")

    signal_path = signal_dir / f"{(target_date or _date.today()):%Y-%m-%d}{signal_suffix}.json"
    signals = None
    for attempt in range(3):
        signals = load_signal(
            signal_dir=signal_dir,
            target_date=target_date,
            schema_path=Path("schemas/signal-v1.json"),
            max_age_min=max_age_min,
            name_suffix=signal_suffix,
        )
        if signals is not None:
            break
        log.info("signal JSON 로드 실패 — 30초 후 재시도 (%d/3)", attempt + 1)
        _time.sleep(30)

    if signals is None:
        # 사유 명시
        if signal_path.exists():
            age_min = (_time.time() - signal_path.stat().st_mtime) / 60
            reason = f"stale (생성 {age_min:.0f}분 전, 한도 60분)"
        else:
            reason = "파일 없음"
        log.warning("signal JSON 로드 최종 실패: %s (%s)", reason, signal_path)
        send_warning(f"signal JSON 로드 실패\n사유: {reason}\nfile: {signal_path.name}")
        return 0

    buys = filter_buy_candidates(signals, min_score=rules.entry_signal_score_min)
    if args.asset_class != "all":
        before = len(buys)
        buys = [b for b in buys if _asset_class(b) == args.asset_class]
        log.info("asset_class=%s 후보 필터: %d → %d", args.asset_class, before, len(buys))
    if not buys:
        blocked = [
            s for s in (signals.get("strategy_signals") or [])
            if int(s.get("strategy_score", 0) or 0) >= rules.entry_signal_score_min
            and s.get("eligible") is False
        ]
        if blocked:
            detail = ", ".join(
                f"{s.get('ticker')}({','.join(s.get('filter_reasons') or []) or '부적격'})"
                for s in blocked
            )
            log.info(
                "score >= %d 후보 %d건 전부 부적격 컷: %s",
                rules.entry_signal_score_min, len(blocked), detail,
            )
        else:
            log.info("score >= %d BUY 후보 없음", rules.entry_signal_score_min)
        return 0

    # 2. 계좌 스냅샷 — KIS REST + DB
    repo = Repo()
    client = KisClient(mode="paper")
    with client.session() as c:
        balance = c.get_balance()
        if balance is None:
            send_critical("KIS 잔고 조회 실패 → 진입 skip")
            return 1

        active_count = len(repo.get_active_positions())
        daily_entries = repo.get_today_entries()

        overseas_cash_usd = float(rules.overseas_paper_capital_usd)
        try:
            ov_balance = c.get_overseas_balance(exchange="NASD", currency="USD")
            if ov_balance and ov_balance.cash > 0:
                overseas_cash_usd = ov_balance.cash / max(ov_balance.price_scale, 1)
        except Exception as e:
            log.info("해외 잔고 조회 skip: %s", e)

        account = AccountSnapshot(
            cash_won=balance.cash,
            open_positions=active_count,
            daily_pnl_pct=0.0,
            daily_entries_today=daily_entries,
            cash_usd=overseas_cash_usd,
        )
        log.info("계좌: cash=%d원, overseas_cash=%.2fUSD, active_positions=%d, 오늘 진입=%d",
                 balance.cash, overseas_cash_usd, active_count, daily_entries)

        # 3. 진입 결정 — 중복 ticker filter
        candidates = [b for b in buys if not repo.is_duplicate(b["ticker"])]
        plans, skips = select_entries(candidates, signals, account, rules, repo=repo)

        log.info("진입 계획 %d건, skip %d건", len(plans), len(skips))
        for s in skips:
            log.info("  SKIP %s %s: %s", s.ticker, s.name, s.reason)

        # 4. 주문 + DB INSERT
        for p in plans:
            if p.asset_class == "overseas_stock":
                price_label = f"{p.currency} {p.entry_price_tick / max(p.price_scale, 1):.2f}"
                sl_label = f"{p.currency} {p.stop_loss_price / max(p.price_scale, 1):.2f}"
                tp_label = f"{p.currency} {p.take_profit_price / max(p.price_scale, 1):.2f}"
            else:
                price_label = f"{p.entry_price_tick:,}원"
                sl_label = f"{p.stop_loss_price:,}원"
                tp_label = f"{p.take_profit_price:,}원"

            log.info("  → BUY %s %s [%s/%s] qty=%d @ %s (size %.1f%%, SL %s, TP %s)",
                     p.ticker, p.name, p.strategy_id, p.asset_class, p.qty, price_label,
                     p.decision.size_pct, sl_label, tp_label)

            if p.asset_class == "overseas_stock":
                result = c.submit_overseas_buy(
                    p.broker_symbol or p.ticker,
                    p.qty,
                    p.entry_price_tick,
                    exchange=p.exchange or "NASD",
                    price_scale=p.price_scale,
                )
            else:
                result = c.submit_buy(
                    p.ticker, p.qty, p.entry_price_tick,
                    order_type="market" if args.carry_over else "limit",
                )
            if not result.accepted:
                send_warning(f"주문 거부 {p.ticker} {p.name}: {result.raw.get('msg1', result.raw)}")
                continue

            # 5. positions DB INSERT (status=PENDING)
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

            send_info(
                f"BUY 주문 접수: {p.name} ({p.ticker}) 주문번호 {result.broker_order_id}\n"
                f"포지션 ID: {pos_id}\n"
                f"전략: {p.strategy_id} (+{p.strategy_score})\n"
                f"가격 {p.entry_price_tick:,} × {p.qty}주 = {p.entry_price_tick * p.qty:,}원\n"
                f"손절 {p.stop_loss_price:,} / 익절 {p.take_profit_price:,}\n"
                f"보유 최대 {p.decision.max_hold_days}거래일\n"
                f"thesis: {p.decision.key_thesis}"
            )

    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
