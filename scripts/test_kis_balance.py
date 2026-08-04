"""KIS REST API PoC — 잔고/시세 조회.

실행 (자택 GUI 터미널):
  cd ~/stock-trader && .venv/bin/python scripts/test_kis_balance.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from src.broker.kis_client import KisClient  # noqa: E402
from src.util.keychain import load_kis_keys, load_telegram_keys  # noqa: E402


def main() -> int:
    print("=== Keychain 로드 ===")
    print(load_kis_keys("paper"))
    print(load_telegram_keys())

    client = KisClient(mode="paper")
    print(f"\n=== KIS base URL: {client.base_url}")
    print(f"=== CANO: {client.cano!r}  ACNT_PRDT_CD: {client.acnt_prdt_cd!r}")

    with client.session() as c:
        print("\n=== get_balance() 호출 ===")
        balance = c.get_balance()
        if balance is None:
            print("balance=None")
        else:
            print(f"cash:       {balance.cash:,}")
            print(f"total_eval: {balance.total_eval:,}")
            print(f"positions:  {len(balance.positions)}")
            for p in balance.positions[:5]:
                print(f"  - {p['ticker']} {p['name']} qty={p['qty']} pnl={p.get('pnl_pct', 0):+.2f}%")
            print(f"raw msg1:   {balance.raw.get('msg1', '')[:200]}")

        print("\n=== get_quote(005930) 호출 — 삼성전자 ===")
        q = c.get_quote("005930")
        if q is None:
            print("quote=None")
        else:
            print(f"current_price: {q.current_price:,}")
            print(f"today open/high/low/prev_close: {q.today_open:,} / {q.today_high:,} / {q.today_low:,} / {q.prev_close:,}")
            print(f"d_change_pct: {q.d_change_pct:+.2f}%")
            print(f"volume: {q.volume:,}")

        print("\n=== get_deposit() — 주문가능금액 ===")
        cash = c.get_deposit()
        print(f"order_psbl_cash: {cash:,}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
