#!/bin/bash
# 자택 맥북 stock-trader 운영 상태 일괄 점검.
# SSH로 실행: ssh macbook-home '~/stock-trader/scripts/healthcheck.sh'
set -euo pipefail
cd "$(dirname "$0")/.."

echo "===1. sysctl tweaks (LaunchDaemon 자동 적용)==="
sysctl net.inet.ip.portrange.first net.inet.tcp.msl 2>&1
echo "기대값: portrange.first=32768, msl=1000"

echo
echo "===2. caffeinate launchd==="
launchctl list | grep caffeinate || echo "(미등록)"

echo
echo "===3. stock-trader launchd 3개==="
for p in com.choeyeonggi.tradeorch com.choeyeonggi.posmonitor com.choeyeonggi.dailyreconciler; do
  launchctl list | grep "$p" || echo "$p (미등록)"
done

echo
echo "===4. stock-signal-bot launchd==="
launchctl list | grep stocksignal || echo "(미등록)"

echo
echo "===5. 외부 HTTPS==="
curl -4 -sS --max-time 5 -o /dev/null -w "stooq: %{http_code}\n" https://stooq.com 2>&1
curl -4 -sS --max-time 5 -o /dev/null -w "krx:   %{http_code}\n" https://data.krx.co.kr 2>&1

echo
echo "===6. socket 통계==="
echo "TIME_WAIT: $(netstat -an | grep TIME_WAIT | wc -l)"
echo "ESTABLISHED: $(netstat -an | grep ESTABLISHED | wc -l)"

echo
echo "===7. SQLite 상태==="
if [ -f data/trades.sqlite ]; then
  echo "DB size: $(du -h data/trades.sqlite | cut -f1)"
  echo "OPEN positions: $(sqlite3 data/trades.sqlite "SELECT count(*) FROM positions WHERE status='OPEN';" 2>/dev/null || echo error)"
  echo "PENDING positions: $(sqlite3 data/trades.sqlite "SELECT count(*) FROM positions WHERE status='PENDING';" 2>/dev/null || echo error)"
  echo "ENTRY decisions: $(sqlite3 data/trades.sqlite "SELECT count(*) FROM llm_decisions WHERE decision_type='ENTRY';" 2>/dev/null || echo error)"
  echo "Unlabeled due ENTRY: $(sqlite3 data/trades.sqlite "SELECT count(*) FROM llm_decisions WHERE decision_type='ENTRY' AND label IS NULL AND eval_due_date IS NOT NULL AND eval_due_date <= date('now','localtime');" 2>/dev/null || echo error)"
  echo "ENTRY by strategy:"
  sqlite3 data/trades.sqlite "SELECT coalesce(strategy_id,'(none)'), count(*) FROM llm_decisions WHERE decision_type='ENTRY' GROUP BY coalesce(strategy_id,'(none)');" 2>/dev/null || true
else
  echo "(DB 없음)"
fi

echo
echo "===8. Kill Switch==="
if [ -f data/KILL_SWITCH ]; then
  echo "ACTIVE:"
  cat data/KILL_SWITCH
else
  echo "OFF"
fi

echo
echo "===9. 최근 로그 마지막 줄==="
for log in data/logs/*.log; do
  [ -f "$log" ] || continue
  echo "--- $(basename $log) ---"
  tail -3 "$log"
done

echo
echo "===10. KIS 토큰 캐시 확인==="
# 구 kiwoom-mcp 빌드 체크는 제거 (2026-07-06) — 브로커는 KIS REST 직접 호출.
KIS_TOKEN_CACHE="$HOME/.kis-token-${KIS_MODE:-paper}.json"
if [ -f "$KIS_TOKEN_CACHE" ]; then
  exp=$(/usr/bin/python3 -c "import json,sys,datetime; d=json.load(open('$KIS_TOKEN_CACHE')); print(datetime.datetime.fromtimestamp(d.get('expires_at',0)).isoformat())" 2>/dev/null || echo "파싱 실패")
  echo "OK: $KIS_TOKEN_CACHE (만료: $exp)"
else
  echo "(캐시 없음 — 다음 실행 시 자동 발급): $KIS_TOKEN_CACHE"
fi
