#!/bin/bash
# Keychain → 환경 변수. ~/Library/LaunchAgents plist의 EnvironmentVariables는
# 평문 저장이라 위험 → wrapper script로 키체인 inject.
#
# 사용:
#   eval $(./scripts/load_secrets_to_env.sh)
#   python -m src.orchestrator
#
# 또는 .env 파일로:
#   ./scripts/load_secrets_to_env.sh > .env
#
# 2026-07-06: Keychain service kiwoom-openapi → kis-openapi 개명 + 구 kiwoom-mcp
# spec(KIWOOM_*) 제거. src/broker/kis_client.py가 읽는 KIS_* 스펙으로 통일.
set -euo pipefail

ks() {
  # security find-generic-password
  /usr/bin/security find-generic-password -s "$1" -a "$2" -w 2>/dev/null || echo ""
}

KIS_MODE="${KIS_MODE:-paper}"

# src/broker/kis_client.py 환경변수 spec:
#   KIS_MODE, KIS_APP_KEY, KIS_APP_SECRET, KIS_CANO, KIS_ACNT_PRDT_CD
ACCOUNT_FULL=$(ks kis-openapi "${KIS_MODE}-account")
echo "export KIS_MODE=$KIS_MODE"  # paper / real
echo "export KIS_APP_KEY=$(ks kis-openapi ${KIS_MODE}-appkey)"
echo "export KIS_APP_SECRET=$(ks kis-openapi ${KIS_MODE}-secret)"
if [ ${#ACCOUNT_FULL} -eq 10 ]; then
  # 10자리 = CANO(8) + ACNT_PRDT_CD(2)
  echo "export KIS_CANO=${ACCOUNT_FULL:0:8}"
  echo "export KIS_ACNT_PRDT_CD=${ACCOUNT_FULL:8:2}"
else
  echo "export KIS_CANO=$ACCOUNT_FULL"
  echo "export KIS_ACNT_PRDT_CD=01"
fi

echo "export TELEGRAM_BOT_TOKEN=$(ks telegram-bot stock-trader)"
echo "export TELEGRAM_CHAT_ID=$(ks telegram-bot stock-trader-chatid)"
