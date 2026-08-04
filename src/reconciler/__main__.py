"""daily-reconciler entrypoint — 평일 16:00 launchd fire.

흐름:
  1. 미체결 잔주문 취소
  2. positions reconciliation (DB vs KIS 잔고 cross-check)
  3. 일일 PnL 집계 → daily_pnl 테이블
  4. SQLite 백업
  5. Telegram 일일 리포트
  6. Kill Switch 자동 평가 (일일 -2% 시 활성)
"""
from __future__ import annotations

import logging
import shutil
import sys
from datetime import date
from pathlib import Path

from src.broker.kis_client import KisClient
from src.guardrails.kill_switch import activate as activate_kill
from src.guardrails.rules import load_rules
from src.notify.telegram import send_critical, send_daily
from src.storage.repository import Repo
from src.util.keychain import load_kis_keys, load_telegram_keys

load_kis_keys()
load_telegram_keys()

log = logging.getLogger("stock-trader.reconciler")
Path("data/logs").mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("data/logs/reconciler.log"),
        logging.StreamHandler(sys.stdout),
    ],
)


def _entry_label(action: str | None, actual_return: float) -> str:
    """5거래일 후 ENTRY 판단 라벨.

    actual_return은 0.03 = +3% 형식.
    BUY: +3% 이상이면 TP, -2% 이하면 FP, 그 사이는 NEUTRAL.
    SKIP: +3% 이상 올랐으면 FN, 아니면 TN.
    """
    if action == "BUY":
        if actual_return >= 0.03:
            return "TRUE_POSITIVE"
        if actual_return <= -0.02:
            return "FALSE_POSITIVE"
        return "NEUTRAL"
    if actual_return >= 0.03:
        return "FALSE_NEGATIVE"
    return "TRUE_NEGATIVE"


def _label_due_entry_decisions(repo: Repo, client: KisClient, today: date) -> int:
    labeled = 0
    for decision in repo.get_due_entry_decisions(today):
        quote = client.get_quote(decision.ticker)
        if quote is None or not quote.current_price or not decision.price_at_decision:
            log.warning("ENTRY 라벨링 skip: quote 없음 %s decision_id=%s", decision.ticker, decision.id)
            continue
        actual_return = quote.current_price / decision.price_at_decision - 1
        label = _entry_label(decision.action, actual_return)
        repo.label_llm_decision(decision.id, label, actual_return)
        labeled += 1
        log.info(
            "ENTRY 라벨링: id=%s %s %s action=%s return=%+.2f%% label=%s",
            decision.id,
            decision.ticker,
            decision.name,
            decision.action,
            actual_return * 100,
            label,
        )
    return labeled


def _calc_cumulative_pnl(total_asset_value: int, capital_baseline: int) -> tuple[int, float]:
    """최초 금액 대비 현재 평가자산(현금+주식)의 누적 손익."""
    cumulative_pnl_won = total_asset_value - capital_baseline
    cumulative_pnl_pct = (cumulative_pnl_won / capital_baseline) * 100 if capital_baseline else 0.0
    return cumulative_pnl_won, cumulative_pnl_pct


def _has_reportable_activity(
    *,
    filled: int,
    cancelled: int,
    trades_opened: int,
    trades_closed: int,
    realized_pnl_won: int,
    cumulative_pnl_won: int,
    sl_hits: int,
    labeled_entries: int,
) -> bool:
    """Telegram 일일 리포트를 보낼 만큼 의미 있는 변화가 있는지 판단."""
    return any(
        value != 0
        for value in (
            filled,
            cancelled,
            trades_opened,
            trades_closed,
            realized_pnl_won,
            cumulative_pnl_won,
            sl_hits,
            labeled_entries,
        )
    )


def run() -> int:
    rules = load_rules(Path("config/trading_rules.yaml"))
    today = date.today()
    repo = Repo()

    client = KisClient(mode="paper")
    with client.session() as c:
        # 1. KIS 잔고 cross-check
        balance = c.get_balance()
        if balance is None:
            send_critical("reconciler: 잔고 조회 실패 → 다음 평일까지 KillSwitch 권장")
            return 1
        log.info("KIS 잔고: cash=%d total_eval=%d positions=%d",
                 balance.cash, balance.total_eval, len(balance.positions))

        filled, cancelled = repo.reconcile_pending_from_balance(balance.positions, today=today)
        if filled or cancelled:
            log.info("PENDING 동기화: OPEN %d건 / CANCELLED %d건", filled, cancelled)

        labeled_entries = _label_due_entry_decisions(repo, c, today)

        # 2. DB 일일 PnL 집계
        trades_closed, realized_pnl_won, sl_hits = repo.get_today_pnl()
        capital_baseline = 30_000_000  # 모의 초기 자본 (config로 빼는 게 좋음)
        total_asset_value = balance.total_eval or balance.cash or capital_baseline  # 현금+주식 총 평가금액
        cumulative_pnl_won, cumulative_pnl_pct = _calc_cumulative_pnl(total_asset_value, capital_baseline)
        realized_pnl_pct = (realized_pnl_won / capital_baseline) * 100 if capital_baseline else 0.0
        trades_opened = repo.get_today_entries()
        log.info(
            "DB PnL: opened=%d closed=%d realized=%+d (%.2f%%) cumulative=%+d (%.2f%%) sl_hits=%d",
            trades_opened,
            trades_closed,
            realized_pnl_won,
            realized_pnl_pct,
            cumulative_pnl_won,
            cumulative_pnl_pct,
            sl_hits,
        )

        # 일일 PnL upsert
        try:
            repo.upsert_daily_pnl(
                trade_date=today,
                capital_start=capital_baseline,
                capital_end=total_asset_value,
                realized_pnl_won=realized_pnl_won,
                realized_pnl_pct=realized_pnl_pct,
                unrealized_pnl_won=total_asset_value - capital_baseline - realized_pnl_won,
                trades_opened=trades_opened,
                trades_closed=trades_closed,
                stop_loss_hits=sl_hits,
                take_profit_hits=0,
                time_stops=0,
                kill_switch_active=0,
            )
        except Exception as e:
            log.warning("daily_pnl upsert 실패: %s", e)

        # 4. Kill Switch 자동 평가
        if realized_pnl_pct <= -rules.daily_loss_pct:
            activate_kill(f"일일 손실 한도 도달 ({realized_pnl_pct:+.2f}%)")
            send_critical(f"🚨 Kill Switch 자동 활성: 일일 {realized_pnl_pct:+.2f}%")

        # 5. SQLite 백업
        db_path = Path("data/trades.sqlite")
        backup_dir = Path("data/backups")
        backup_dir.mkdir(exist_ok=True)
        if db_path.exists():
            backup_path = backup_dir / f"trades-{today:%Y-%m-%d}.sqlite"
            shutil.copy(db_path, backup_path)
            log.info("DB 백업: %s", backup_path)

        # 6. 30일 이전 백업 정리
        for old in sorted(backup_dir.glob("trades-*.sqlite"))[:-30]:
            old.unlink()
            log.info("오래된 백업 삭제: %s", old)

        # 7. 일일 리포트
        msg = (
            f"📊 일일 리포트 — {today}\n"
            f"평가자산(현금+주식): {total_asset_value:,}원\n"
            f"누적 손익: {cumulative_pnl_won:+,}원 ({cumulative_pnl_pct:+.2f}%)\n"
            f"일일 실현 PnL: {realized_pnl_won:+,}원 ({realized_pnl_pct:+.2f}%)\n"
            f"신규 진입: {trades_opened}건\n"
            f"청산: {trades_closed}건\n"
            f"미체결 동기화: OPEN {filled}건 / CANCELLED {cancelled}건\n"
            f"ENTRY 라벨링: {labeled_entries}건"
        )
        if _has_reportable_activity(
            filled=filled,
            cancelled=cancelled,
            trades_opened=trades_opened,
            trades_closed=trades_closed,
            realized_pnl_won=realized_pnl_won,
            cumulative_pnl_won=cumulative_pnl_won,
            sl_hits=sl_hits,
            labeled_entries=labeled_entries,
        ):
            send_daily(msg)
        else:
            log.info("일일 리포트 Telegram 알림 생략: 거래/손익/미체결/ENTRY 라벨링 변화 없음")
        log.info(msg)

    return 0


if __name__ == "__main__":
    sys.exit(run())
