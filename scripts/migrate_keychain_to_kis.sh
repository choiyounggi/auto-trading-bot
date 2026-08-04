#!/bin/bash
# kiwoom-openapi → kis-openapi Keychain service 이름 마이그레이션 (값 복사).
# launchd(GUI 세션) 컨텍스트에서 실행해야 함 — SSH 비대화 셸은 -w 읽기가 ACL로 차단.
# 시크릿 값은 로그에 남기지 않는다 (길이 + sha256 앞 12자만).
LOG="$HOME/stock-trader/data/logs/keychain_migration.log"
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') kiwoom-openapi -> kis-openapi 마이그레이션 ==="
  ok=0; fail=0
  for a in paper-appkey paper-secret paper-account real-appkey real-secret real-account; do
    v=$(/usr/bin/security find-generic-password -s kiwoom-openapi -a "$a" -w 2>/dev/null)
    if [ -z "$v" ]; then
      echo "$a: 원본 읽기 실패 (빈 값 또는 ACL)"
      fail=$((fail+1))
      continue
    fi
    /usr/bin/security add-generic-password -U -s kis-openapi -a "$a" -w "$v"
    v2=$(/usr/bin/security find-generic-password -s kis-openapi -a "$a" -w 2>/dev/null)
    if [ "$v" = "$v2" ]; then
      echo "$a: OK len=${#v} sha=$(printf %s "$v" | /usr/bin/shasum -a 256 | cut -c1-12)"
      ok=$((ok+1))
    else
      echo "$a: MISMATCH (복사본 재검증 실패)"
      fail=$((fail+1))
    fi
  done
  echo "결과: OK=$ok FAIL=$fail"
} >> "$LOG" 2>&1
