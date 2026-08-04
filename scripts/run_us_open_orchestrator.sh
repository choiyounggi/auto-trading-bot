#!/usr/bin/env bash
# 미국장 개장 직후 해외주식 진입 판단/주문 job.
set -euo pipefail
cd "$(dirname "$0")/.."

# signal job보다 5~30분 뒤만 통과. DST/표준시간 모두 대응.
if ! python3 - <<'PY'
from datetime import datetime
from zoneinfo import ZoneInfo
now = datetime.now(ZoneInfo('Asia/Seoul')).astimezone(ZoneInfo('America/New_York'))
open_et = now.replace(hour=9, minute=30, second=0, microsecond=0)
delta_min = (now - open_et).total_seconds() / 60
raise SystemExit(0 if now.weekday() < 5 and 5 <= delta_min <= 35 else 1)
PY
then
  echo "not US open orchestrator window; skip"
  exit 0
fi

if [ -d ".venv" ]; then
  source .venv/bin/activate
fi

exec python -m src.orchestrator --asset-class overseas_stock --signal-max-age-min 90 "$@"
