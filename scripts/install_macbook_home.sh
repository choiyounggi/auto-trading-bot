#!/bin/bash
# 자택 맥북 배포 자동화. stock-signal-bot 패턴 재활용.
#
# 사용법:
#   ./scripts/install_macbook_home.sh sync       — 코드 동기화만
#   ./scripts/install_macbook_home.sh venv       — 원격 venv 생성/갱신
#   ./scripts/install_macbook_home.sh migrate    — SQLite 마이그레이션
#   ./scripts/install_macbook_home.sh launchd    — launchd 3개 plist 설치
#   ./scripts/install_macbook_home.sh test       — 원격 pytest
#   ./scripts/install_macbook_home.sh all        — sync + venv + migrate + launchd + test
#   ./scripts/install_macbook_home.sh unload     — launchd 해제
set -euo pipefail

REMOTE="${REMOTE:-macbook-home}"
REMOTE_DIR="${REMOTE_DIR:-/Users/choeyeong-gi/stock-trader}"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Python 3.11 binary (자택 맥북은 python.org 공식 framework 설치)
REMOTE_PYTHON="${REMOTE_PYTHON:-/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11}"

sync_project() {
  echo "===rsync to $REMOTE:$REMOTE_DIR==="
  rsync -avz --delete \
    --exclude='.venv/' \
    --exclude='__pycache__/' \
    --exclude='.pytest_cache/' \
    --exclude='data/logs/' \
    --exclude='data/backups/' \
    --exclude='data/exports/' \
    --exclude='data/signals/' \
    --exclude='data/trades.sqlite*' \
    --exclude='KILL_SWITCH' \
    --exclude='.git/' \
    "$LOCAL_DIR/" "$REMOTE:$REMOTE_DIR/"
}

setup_venv_remote() {
  echo "===원격 venv 생성/갱신==="
  ssh "$REMOTE" "cd $REMOTE_DIR && $REMOTE_PYTHON -m venv .venv && .venv/bin/pip install --upgrade pip -q && .venv/bin/pip install -e '.[dev]' -q"
}

run_migrate() {
  echo "===SQLite 마이그레이션==="
  ssh "$REMOTE" "cd $REMOTE_DIR && mkdir -p data && [ -f data/trades.sqlite ] || sqlite3 data/trades.sqlite < data/migrations/0001_init.sql"
}

install_launchd() {
  echo "===launchd 3개 plist 설치==="
  for plist in com.choeyeonggi.tradeorch com.choeyeonggi.posmonitor com.choeyeonggi.dailyreconciler; do
    echo "--- $plist ---"
    # 1. plist의 __PROJECT_DIR__ placeholder 치환 후 원격 위치 복사
    sed "s|__PROJECT_DIR__|$REMOTE_DIR|g" "$LOCAL_DIR/plists/$plist.plist" | \
      ssh "$REMOTE" "cat > ~/Library/LaunchAgents/$plist.plist"
    # 2. unload 후 reload
    ssh "$REMOTE" "launchctl bootout gui/501/$plist 2>/dev/null; launchctl bootstrap gui/501 ~/Library/LaunchAgents/$plist.plist"
    ssh "$REMOTE" "launchctl list | grep $plist"
  done
}

run_test() {
  echo "===원격 pytest==="
  ssh "$REMOTE" "cd $REMOTE_DIR && .venv/bin/pytest tests/ -q"
}

unload_launchd() {
  echo "===launchd 3개 해제==="
  for plist in com.choeyeonggi.tradeorch com.choeyeonggi.posmonitor com.choeyeonggi.dailyreconciler; do
    ssh "$REMOTE" "launchctl bootout gui/501/$plist 2>/dev/null; rm -f ~/Library/LaunchAgents/$plist.plist"
  done
}

case "${1:-all}" in
  sync)     sync_project ;;
  venv)     setup_venv_remote ;;
  migrate)  run_migrate ;;
  launchd)  install_launchd ;;
  test)     run_test ;;
  unload)   unload_launchd ;;
  all)      sync_project && setup_venv_remote && run_migrate && install_launchd && run_test ;;
  *) echo "사용법: $0 {sync|venv|migrate|launchd|test|unload|all}"; exit 1 ;;
esac

echo "===완료==="
