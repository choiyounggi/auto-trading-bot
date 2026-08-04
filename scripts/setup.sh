#!/bin/bash
# Phase 0 — 로컬 환경 세팅. 외부 가입과 무관, 단위 테스트까지 동작.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3.11}"

echo "===1. venv 생성==="
if [ ! -d .venv ]; then
  $PYTHON -m venv .venv
fi

echo "===2. 의존성 설치==="
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -e ".[dev]" -q

echo "===3. SQLite 마이그레이션==="
mkdir -p data
if [ ! -f data/trades.sqlite ]; then
  sqlite3 data/trades.sqlite < data/migrations/0001_init.sql
  echo "  data/trades.sqlite 생성 완료"
else
  echo "  data/trades.sqlite 이미 존재 — skip"
fi

echo "===4. 단위 테스트==="
.venv/bin/pytest tests/

echo "===완료==="
echo "다음 단계: ~/Desktop/stock/10-tasks.md 참고"
