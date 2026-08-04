# stock-trader

LLM 기반 한국 주식 완전자동매매 시스템.

> **상세 설계 / 철학 / 가드레일 / 로드맵**: `../` 상위 폴더의 [README.md](../README.md) 참조.

## 빠른 시작 (Phase 0)

```bash
cd ~/stock-trader

# 1. venv (Python 3.11+)
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. 단위 테스트 (외부 의존성 없이 동작)
pytest

# 3. SQLite 마이그레이션
sqlite3 data/trades.sqlite < data/migrations/0001_init.sql

# 4. 환경 변수 (Keychain → .env 추출 wrapper script)
./scripts/load_secrets_to_env.sh > .env
```

## 디렉토리

```
src/
  orchestrator/   16:35 진입 결정 + 매수 주문
  monitor/        정규장 30분 동적 조정 + 청산
  reconciler/     16:00 일일 정리 + PnL 리포트
  broker/         KIS(한국투자증권) REST 클라이언트 + 호가 보정
  llm/            Claude/pi CLI 호출 + Pydantic 스키마
  guardrails/     clamp + KillSwitch
  storage/        SQLAlchemy ORM
  notify/         Telegram
  universe/       거래 가능 종목 필터
config/
  trading_rules.yaml    운영 파라미터
plists/
  com.choeyeonggi.*.plist  launchd job 정의
data/
  trades.sqlite          영속 데이터
  signals/               stock-signal-bot JSON 사본
  logs/
  backups/
schemas/
  signal-v1.json         stock-signal-bot 신호 JSONSchema
tests/                   단위 테스트
```

## 운영 상태

| Phase | 상태 |
|------|------|
| Phase 0 (외부 가입) | 완료 — 한국투자증권(KIS) OpenAPI + Telegram 봇 (Keychain service `kis-openapi`) |
| Phase 1 (핵심 모듈) | 진행 중 (골격 작성) |
| Phase 2 (signal JSON 연동) | 대기 |
| Phase 3 (paper trading) | 대기 |

`../06-phases.md` 상세 로드맵.

## 의존 시스템

- **stock-signal-bot** (`~/stock-signal-bot/`): 평일 16:30 KRX 신호 + LLM 분석 → JSON dump
- **KIS OpenAPI**: 한국투자증권 REST 직접 호출 (`src/broker/kis_client.py`). 자격증명은 macOS Keychain service `kis-openapi` (2026-07-06 `kiwoom-openapi`에서 개명 — 초기 kiwoom-mcp 시절 네이밍 잔재 정리)
- **Claude CLI**: Sonnet 진입/모니터 호출 (Pro/Max 구독)
- **pi CLI**: Codex 5.5 fallback (Earendil pi-coding-agent)
