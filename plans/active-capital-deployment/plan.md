# active-capital-deployment

Goal: 유휴 현금을 **목표 가동률 90%**까지 적극 투입한다. 사이징 기준을 예수금에서
총자산으로 바꾸고, 장중 30분 간격 재배치 경로로 청산 후 회수된 현금을 같은 날 다시
투입한다. 더불어 텔레그램 `/history`로 청산 내역과 손익 요약을 조회한다.

Acceptance criteria:
1. `compute_capital_plan`이 총자산·투자중·가동률·배치가능액을 반환하고, 배치가능액은
   매수여력(`ord_psbl_cash`)을 절대 넘지 않는다.
2. 한 번의 `select_entries` 배치가 만드는 주문 명목 합계가 배치가능액을 넘지 않는다.
3. `kis-trader run cashDeploy` (= `python -m src.orchestrator --deploy-cash`)가
   가동률 미달분만큼 추가 진입하고, 후보 부족이면 경고 1회 후 정상 종료(exit 0)한다.
4. `cashDeploy` launchd 잡이 평일 09:30~15:00 30분 간격 12회로 설치된다.
5. 텔레그램 `/history [N]`이 최근 청산 N건과 승률·누적손익을 반환한다.
6. `pytest -q` 321건 + `npm test` 365건이 **전부 통과한 상태를 유지**하고, 새 테스트가
   추가된다. 기존 테스트를 약화시키지 않는다.

Stack: Python 3.12+ (pydantic, SQLAlchemy 2.x, pytest), TypeScript 5.6 / Node 20+
(`node --test`), SQLite (WAL), macOS launchd. 국내 KIS **paper** 계좌 한정.

**적용 범위 경계:** 해외(`overseas_stock`) 경로와 실거래(real) 모드는 이번 변경에서
건드리지 않는다. 해외 분기는 이미 자체 `capital`(USD)을 쓰므로 그대로 둔다.

---

## Decisions

| # | Decision | Choice | Wiki basis |
|---|----------|--------|------------|
| D1 | 총자산의 출처 | `Balance.total_eval` (KIS `tot_evlu_amt`). `Balance.cash`(`prvs_rcdl_excc_amt`)는 D+2 가수도정산금액이라 총자산 기준으로 부적합 | `[no-wiki]` — `src/broker/kis_client.py:311` 도메인 사실 |
| D2 | 매수여력의 출처 | `KisClient.get_deposit()` (`ord_psbl_cash`). 조회 실패(0 또는 예외) 시 `Balance.cash`로 폴백 | `[no-wiki]` — `src/broker/kis_client.py:400` |
| D3 | 투자중 금액 | `sum(p["eval_amt"] for p in balance.positions)`. 총평가에서 예수금을 빼서 역산하지 않는다 | `[no-wiki]` — 예수금 정의 차이에 흔들리지 않게 |
| D4 | 신규 파라미터 전달 형태 | `evaluate_candidate` / `select_entries`에 추가하는 파라미터는 전부 **keyword-only** (bare `*` 뒤에 선언) | backend-common-change-impact-call-site-enumeration |
| D5 | `AccountSnapshot` 신규 필드 | 기본값을 갖되, **열거된 생산자 5곳을 전부 명시적으로 갱신**한다. 기본값에 맡기지 않는다 | backend-common-change-impact-call-site-enumeration (edge: "adds a parameter with a default → 기본값이 아무도 고르지 않은 영구 동작이 된다") |
| D6 | 신규 config 키의 검증 | 각 신규 키마다 **값을 바꾸면 동작이 바뀐다**는 테스트를 1개 이상 둔다. 파싱만 확인하는 테스트는 인정하지 않는다 | backend-common-api-design-unenforced-declarations ("recorded but unenforced") |
| D7 | 미체결 주문 취급 | PENDING 포지션의 명목금액(`entry_price_target × entry_qty`)을 **투자중에 합산**한다 | backend-common-jobs-idempotent-handlers (스케줄 재실행이 같은 현금을 두 번 쓰는 것을 막는 유일한 수단) |
| D8 | 재배치 잡 중복 실행 | `cashDeploy` JobSpec은 `guarded: true`. 추가로 틱당 LLM 평가 후보를 **4개로 상한** → 최악 4×180s = 12분 < 락 만료 15분 < 스케줄 간격 30분 | backend-common-jobs-scheduled-job-overlap ("Pair skip-on-overlap with a hang guard", "Job duration approaches the schedule interval → 작업을 줄여라") |
| D9 | 재배치 일일 예산 | 재배치는 `quota_override = rules.max_daily_entries + rules.cash_deploy_max_daily_entries` (12+6=18)로 호출한다. `select_entries`가 `quota - daily_entries_today`를 쓰므로, 이는 **아침 예산 위에 얹는 추가 예산**이 된다 | `[no-wiki]` — 같은 카운터를 그대로 쓰면 아침 진입이 예산을 소진해 재배치가 무력화된다. 반대로 진짜 별도 카운터를 두려면 `repository.py`에 "오늘의 재배치 진입 수" 조회를 추가해야 하는데, 그 파일은 t3 단독 소유라 충돌한다. 합산 상한은 새 조회 없이 같은 목적을 달성한다 |
| **D9a** | **조기 게이트도 같은 상한을 본다** | `select_entries` §2의 `if account.daily_entries_today >= rules.max_daily_entries: return [], [daily_entry_limit ...]` 는 `quota_override`를 무시하므로 D9를 무력화한다. **유효 상한을 한 번 계산해 게이트와 quota가 같은 값을 쓰게 한다**: `effective_max_entries = quota_override if quota_override is not None else rules.max_daily_entries` | `[no-wiki]` — **2026-08-13 t2 워커가 발견한 계획 결함.** 태스크 03이 `quota =` 줄만 고치고 조기 게이트를 놓쳤다. 재현: `daily_entries_today=12, max_daily_entries=12, quota_override=18` → `plans=[]`, 전종목 `daily_entry_limit`. 소유권: `entry_decision.py`를 건드리는 다른 실행 중 태스크가 없고 t2 브랜치가 t1 커밋의 자손이므로 **t2가 고친다** |
| D10 | 가드레일 우회 | Kill Switch·시장 레짐 게이트·중복 종목 필터를 재배치 경로에도 **그대로** 적용. 가동률을 채우려 우회하지 않는다 | `[no-wiki]` — 영기 확정 |
| D11 | 후보 부족 시 | 미달을 허용한다. 지수 ETF 채움·피라미딩 모두 하지 않는다. 가동률이 `underrun_warn_pct` 아래면 **하루 1회** 텔레그램 경고 | `[no-wiki]` — 영기 확정 |
| D12 | 배치 예산 소진 | `select_entries`가 `remaining_budget_won`을 들고 다니며 채택된 계획의 명목금액만큼 차감. 1주도 못 사면 `budget_exhausted`로 skip | `[no-wiki]` — 없으면 한 배치가 배치가능액을 초과 발주함 |
| D13 | 수량 상한 | `qty = min(size_qty, risk_qty, budget_qty)` 3중 하한. `budget_qty = remaining_budget_won // entry_price` | `[no-wiki]` — D2/D12의 귀결 |
| D14 | SQLite 동시 접근 | `get_engine`이 커넥션마다 `busy_timeout=5000`, 최초 1회 `journal_mode=WAL`, `foreign_keys=ON`을 건다 | databases-sqlite-concurrent-access-for-a-read-api |
| D15 | 파라미터 상향값 | `max_size_pct 10→15`, `min_size_pct 3→5`, `max_position_count 12→18`, `max_daily_entries 8→12`, 전략별 `max_daily_entries 2→3`. `risk_per_trade_pct`는 0.5 유지 | `[no-wiki]` — 아래 "상향값 근거" 참조 |
| D16 | 목표/버퍼 | `capital.target_utilization_pct: 90`, `capital.cash_buffer_pct: 10` | `[no-wiki]` — 영기 확정 |
| D17 | LLM 프롬프트 | `국내 가용 자본: {cash_won}` 한 줄을 총자산·투자중·가동률·이번 배치 가능액으로 교체하고, **`size_pct`가 총자산 대비 비율임을 명시**한다 | `[no-wiki]` — 기준을 바꾸고 프롬프트를 안 바꾸면 LLM 사이징 감이 어긋남 |
| D18 | launchd 스케줄 표현 | 기존 `JobSpec.times` 배열 사용 (09:30 … 15:00, 30분 간격 12개). 새 schedule 타입을 만들지 않는다 | platforms-processes-background-services |
| D19 | 고정 문자열 계약 | CLI 플래그 `--deploy-cash`, JobName `cashDeploy`, 로그 `cashDeploy.log` — 정확히 이 철자 | `[no-wiki]` — t2/t4 병렬 실행의 유일한 접점 |

### 상향값 근거 (D15)

- 시드 3,000만 기준 `max_size_pct 15%` = 종목당 최대 450만, `min_size_pct 5%` = 최소 150만.
- `max_position_count 18` × `min_size_pct 5%` = 90% — **최소 사이즈로도 목표 가동률에 도달 가능**.
- 리스크 cap과의 정합성: `risk_per_trade_pct 0.5%` ÷ 손절폭으로 계산한 상한은
  손절 1.5%에서 33%, 2.5%에서 20%, 3.0%(최대)에서 16.7%. **전 구간에서 15%보다 크므로
  `size_pct`가 지배**하고 리스크 예산은 안전망으로만 남는다. 이 부등식이 깨지면
  `size_pct`를 올려도 수량이 늘지 않으므로, 값을 바꿀 때 반드시 다시 계산한다.

### `[no-wiki]` ingest 후보

- D1/D2/D3 — "브로커 잔고 API의 여러 금액 필드 중 어느 것이 사이징 기준이고 어느 것이
  발주 상한인가"는 KIS 고유가 아니라 증권 API 일반의 함정. `backend/common/integrations/`
  ingest 후보.
- D7 — "미체결 주문을 유휴 현금으로 오인해 같은 현금을 두 번 쓰는 스케줄 잡".
  `backend/common/jobs/idempotent-handlers.md`의 금융 사례로 추가 후보.

---

## 열거된 계약 변경 지점

방법: `grep -rn "<callee>" src tests scripts` (테스트·스크립트 포함, 위키 지침대로
파라미터명이 아니라 **callee 이름**으로 열거).

`AccountSnapshot(` 생산자 — **5곳**:

| # | 위치 | 담당 태스크 |
|---|------|------------|
| 1 | `src/orchestrator/__main__.py:180` | 06 |
| 2 | `tests/test_entry_risk_sizing.py:10` (`_account()` 헬퍼 — 팬아웃 지점) | 03 |
| 3 | `tests/test_entry_risk_sizing.py:120` | 03 |
| 4 | `tests/test_entry_risk_sizing.py:201` | 03 |
| 5 | `scripts/test_entry_decision.py:70` | 04 |

`select_entries(` 호출부 — **1곳**: `src/orchestrator/__main__.py:192` (태스크 06).
`evaluate_candidate(` 호출부 — **8곳**: `entry_decision.py:442`(내부), 테스트 6곳(03),
`scripts/test_entry_decision.py:97`(04).

편집 후 같은 열거를 재실행해 **구계약 호출부 0건**을 확인하는 것이 완료 조건이다.

---

## Task order

| Task | 내용 | Depends on | Parallel-ok | orchestrate task |
|------|------|-----------|-------------|------------------|
| 01-config-and-rules | 신규 config 키 + 로더 + 파라미터 상향 | — | 02와 병렬 불가(02가 rules 필드 사용) | t1 |
| 02-capital-module | `compute_capital_plan` | 01 | — | t1 |
| 03-sizing-base-switch | 사이징 기준 전환 + 배치 예산 | 02 | — | t1 |
| 04-prompt-and-smoke-script | LLM 프롬프트 + 진단 스크립트 | 03 | — | t1 |
| 05-cash-deploy-core | `run_cash_deploy` | 04 (t1 전체) | 07·10과 병렬 | t2 |
| 06-cash-deploy-wiring | `--deploy-cash` + `__main__` 배선 | 05 | — | t2 |
| 07-sqlite-read-safety | `get_engine` 동시성 pragma | — | t1·t4와 병렬 | t3 |
| 08-history-queries | 청산 조회 + 요약 | 07 | — | t3 |
| 09-telegram-history-command | `/history` | 08 | — | t3 |
| 10-job-key-registration | `JobName cashDeploy` | — | t1·t3와 병렬 | t4 |
| 11-launchd-job-spec | JobSpec + 스케줄 + plist | 10 | — | t4 |
| 12-docs-and-version | README + 0.4.0 | 01–11 | — | t5 |
