# Task 12: README를 실제 동작에 맞추고 0.4.0으로 올린다

## Objective
README가 가동률 기반 투입, `cashDeploy` 잡, `/history` 명령, 새 config 키를 실제
구현된 대로 설명한다. `package.json` 버전이 `0.4.0`이 된다.

## Wiki pages (read these first, only these)
- `wiki/qa/process/...`의 "sourcing deliverable documents from generated artifacts"
  계열 페이지 — 문서를 **머지된 코드에서 확인해서** 쓰고, 계획서에서 베껴 쓰지 않기
  위해. (도메인 인덱스 `wiki/qa/index.md`에서 "load when"이 맞는 페이지를 고를 것.
  맞는 페이지가 없으면 이 태스크는 위키 없이 진행하고 `[no-wiki]`로 기록한다.)

## Inputs
- 머지된 통합 브랜치의 실제 코드 — **이 태스크의 유일한 진실 공급원**
- 기존 파일: `README.md`(22KB, 한국어), `package.json`(version, description)
- 참고: `.orchestration/plans/active-capital-deployment/plan.md`의 Decisions 표
  (배경 설명용이지, 동작 기술의 근거로 삼지 말 것)

## Steps
1. **먼저 코드를 읽는다.** 아래를 실제 파일에서 확인하고, 확인한 값만 문서에 쓴다:
   - `config/trading_rules.yaml` — `capital.target_utilization_pct`,
     `capital.cash_buffer_pct`, `cash_deploy` 블록 5개 키의 **실제 값**
   - `cli/launchd.ts` `JOBS.cashDeploy` — 실제 스케줄·args·log·guarded
   - `src/agent/telegram_agent.py` — `/history`의 실제 인자 형태와 기본/최대 건수
   - `src/orchestrator/cash_deploy.py` — 실제 동작 순서와 경고 조건
   계획서에 적힌 값과 코드가 다르면 **코드가 이긴다**. 다르면 그 사실을 보고한다.
2. README의 잡 목록(현재 8개 잡을 설명하는 표/절)에 `cashDeploy` 행을 추가한다.
   기존 행과 같은 열 구성(잡 이름 / 스케줄 / 하는 일)을 지킨다.
3. 텔레그램 명령 목록 절에 `/history [N]` 행을 추가한다. 기존 `/balance`, `/positions`,
   `/status`, `/buyable`, `/mode`, `/buy`, `/sell` 행과 같은 형식.
4. 운영 파라미터를 설명하는 절에 **가동률 개념**을 짧게 넣는다 (3~5줄):
   - 사이징 기준이 예수금이 아니라 총자산(`tot_evlu_amt`)이라는 것
   - 목표 가동률과 현금 버퍼의 의미
   - 실제 발주는 매수여력(`ord_psbl_cash`)을 넘지 않는다는 것
   - **후보가 부족하면 목표에 미달할 수 있고, 그때는 채우지 않고 경고만 한다**는 것
     — 이건 사용자가 명시적으로 고른 트레이드오프라 문서에 남아야 한다.
5. 상향된 파라미터 값(`max_size_pct` 등)이 README에 하드코딩돼 있으면 갱신한다.
   `grep -n "10%\|max_size_pct\|max_position_count\|max_daily_entries" README.md`로 찾을 것.
6. `package.json`의 `version`을 `0.3.0` → `0.4.0`으로 올린다. minor 인상 근거:
   기능 추가이고 기존 config·CLI 호환이 깨지지 않는다 (`cash_deploy` 블록이 없는
   구버전 config도 기본값으로 로드된다).
7. `package.json`의 `description`은 그대로 둔다 — 여전히 정확하다.
8. 계획 산출물을 저장소 관례에 맞춰 남긴다:
   `.orchestration/`은 gitignore 대상이므로, `plans/active-capital-deployment/`로
   **복사**한다 (`plan.md` + `tasks/*.md`). 기존 `plans/npm-cli-packaging/`,
   `plans/state-relocation-and-cutover/`와 같은 관례다.

## Deliverables
- `README.md` (수정)
- `package.json` (version만)
- `plans/active-capital-deployment/` (계획 문서 복사)

## Verify
문서 태스크라 단위 테스트가 아니라 **대조 검사**로 검증한다. 각 항목을 명령으로 확인하고
결과를 보고에 적는다:

1. `grep -n "cashDeploy" README.md` → 1건 이상.
2. `grep -n "/history" README.md` → 1건 이상.
3. `grep -n "가동률" README.md` → 1건 이상.
4. **값 대조**: README에 적은 `target_utilization_pct` 값이
   `grep -n "target_utilization_pct" config/trading_rules.yaml`의 값과 같다.
   나머지 `cash_deploy.*` 5개도 같은 방식으로 대조한다.
5. **스케줄 대조**: README에 적은 cashDeploy 스케줄이 `cli/launchd.ts`의
   `CASH_DEPLOY_TIMES` 정의와 일치한다 (09:30~15:00, 30분 간격, 평일).
6. `node -e "console.log(require('./package.json').version)"` → `0.4.0`.
7. `ls plans/active-capital-deployment/tasks/ | wc -l` → 12.
8. 회귀: `npm test`와 `.venv/bin/python -m pytest -q`가 여전히 전부 통과한다
   (문서 변경이 `tests/test_repo_hygiene.py` 같은 저장소 위생 테스트를 깨지 않는지).
9. 경계: README에 남아 있는 **구값**이 없는지 —
   `grep -n "max_size_pct: 10\|max_position_count: 12\|max_daily_entries: 8" README.md`
   가 0건.

## Out of scope
- 코드 변경 일체. 이 태스크는 문서와 버전만 만진다. 문서를 쓰다 코드 결함을 발견하면
  **고치지 말고 보고**한다.
- CHANGELOG 신설 — 이 저장소에는 없다. 만들지 않는다.
- npm publish / git tag — 사용자가 직접 한다.
