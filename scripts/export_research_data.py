#!/usr/bin/env python3
"""paper 운용 연구 데이터 CSV export.

실행 (프로젝트 루트에서):
  .venv/bin/python scripts/export_research_data.py

출력:
  data/exports/YYYYMMDD_HHMMSS/{llm_decisions,positions,orders,daily_pnl}.csv
"""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "trades.sqlite"
TABLES = ("llm_decisions", "positions", "orders", "daily_pnl", "system_events")


def export_table(con: sqlite3.Connection, table: str, out_dir: Path) -> int:
    cur = con.execute(f"SELECT * FROM {table}")
    rows = cur.fetchall()
    out_path = out_dir / f"{table}.csv"
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([d[0] for d in cur.description])
        writer.writerows(rows)
    return len(rows)


def main() -> int:
    if not DB_PATH.exists():
        raise SystemExit(f"DB 없음: {DB_PATH}")
    out_dir = ROOT / "data" / "exports" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    try:
        for table in TABLES:
            count = export_table(con, table, out_dir)
            print(f"{table}: {count} rows → {out_dir / (table + '.csv')}")
    finally:
        con.close()
    print(f"export_dir={out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
