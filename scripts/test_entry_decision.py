"""LLM 진입 결정 테스트 — 실 주문 X.

목적: signal JSON 기반 LLM 결정을 dry-run으로 확인.
- size_pct (보수적이지 않은지)
- confidence (8.5+ 통과 여부)
- SL/TP / max_hold_days
- key_thesis / key_risks

실행 (자택 GUI 터미널):
  cd ~/stock-trader && .venv/bin/python scripts/test_entry_decision.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

from src.broker.kis_client import KisClient  # noqa: E402
from src.guardrails.rules import load_rules  # noqa: E402
from src.orchestrator.entry_decision import (  # noqa: E402
    AccountSnapshot,
    evaluate_candidate,
)
from src.orchestrator.signal_loader import load_signal  # noqa: E402
from src.util.keychain import load_kis_keys, load_telegram_keys  # noqa: E402


def main() -> int:
    print("=== Keychain 로드 ===")
    print(load_kis_keys("paper"))
    load_telegram_keys()

    # 1주일 max_age로 lenient하게 — 오늘 또는 어제 JSON 모두 허용
    signals = load_signal(
        signal_dir=Path.home() / "stock-signal-bot" / "data" / "signals",
        schema_path=Path("schemas/signal-v1.json"),
        max_age_min=60 * 24 * 7,  # 1주
    )
    if signals is None:
        print("signal JSON 없음")
        return 1

    rules = load_rules(Path("config/trading_rules.yaml"))
    # 보수성 테스트 — score 임계 우회 (모든 BUY 후보 LLM 호출)
    rules.entry_signal_score_min = 0

    all_buys = signals.get("buys", [])
    print(f"\n=== signal date: {signals.get('date')} ===")
    print(f"=== BUY 전체: {len(all_buys)}건 ===")
    for b in all_buys:
        print(f"  - {b['ticker']} {b['name']} (score {b['score']}) triggers={b.get('triggers', [])}")

    # 계좌 정보
    client = KisClient(mode="paper")
    with client.session() as c:
        balance = c.get_balance()
        if balance is None:
            print("balance 조회 실패")
            return 1
        print(f"\n=== 계좌 ===")
        print(f"  cash:       {balance.cash:,}")
        print(f"  total_eval: {balance.total_eval:,}")
        print(f"  positions:  {len(balance.positions)}")

    account = AccountSnapshot(
        cash_won=balance.cash,
        open_positions=0,           # dry-run — 가상 0
        daily_pnl_pct=0.0,
        daily_entries_today=0,
    )

    # 각 후보별 LLM 결정
    print(f"\n=== rules ===")
    print(f"  min_confidence: {rules.entry_min_confidence}")
    print(f"  size_pct cap: {rules.min_size_pct}~{rules.max_size_pct}%")
    print(f"  SL cap: {rules.min_stop_loss_pct}~{rules.max_stop_loss_pct}%")
    print(f"  TP cap: {rules.min_take_profit_pct}~{rules.max_take_profit_pct}%")
    print(f"  max_hold_days: {rules.max_hold_days} 영업일")
    print(f"  self-consistency: {rules.entry_self_consistency}회 호출")

    for cand in all_buys:
        print(f"\n{'='*60}")
        print(f"[{cand['ticker']} {cand['name']}] score={cand['score']}")
        print(f"  triggers: {', '.join(cand.get('triggers', []))}")
        sb = cand.get("short_balance")
        if sb:
            print(f"  공매도잔고: {sb['latest_pct']:.2f}% (5d {sb['pct_5d_change']:+.2f}, 20d {sb['pct_20d_change']:+.2f})")
        ps = cand.get("panel_summary", {})
        print(f"  최근: 종가 {ps.get('last_close', 0):,} {ps.get('d_change_pct', 0):+.2f}% / 5d 거래량비 {ps.get('vol_ratio_5d', 0):.2f}배")

        print(f"\n  >>> LLM 호출 (Self-consistency {rules.entry_self_consistency}회) — Claude CLI 3~6분 소요")
        plan, reason = evaluate_candidate(cand, signals, account, rules)
        if plan:
            d = plan.decision
            print(f"\n  ✅ BUY 결정")
            print(f"    confidence: {d.confidence}/10")
            print(f"    size_pct:   {d.size_pct:.1f}% (총자본 대비)")
            print(f"    SL:         -{d.stop_loss_pct:.2f}%")
            print(f"    TP:         +{d.take_profit_pct:.2f}%")
            print(f"    max_hold:   {d.max_hold_days} 영업일")
            print(f"    qty:        {plan.qty}주")
            print(f"    entry_price:{plan.entry_price_tick:,} 원")
            print(f"    stop_loss:  {plan.stop_loss_price:,} 원")
            print(f"    take_profit:{plan.take_profit_price:,} 원")
            print(f"    total_won:  {plan.entry_price_tick * plan.qty:,}")
            print(f"\n    key_thesis: {d.key_thesis}")
            print(f"    key_risks:  {d.key_risks}")
            print(f"    watch:      {d.watch_signals}")
        else:
            print(f"\n  ❌ SKIP — reason: {reason}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
