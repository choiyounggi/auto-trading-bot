# Task 11: `cashDeploy` launchd JobSpec과 30분 간격 스케줄을 정의한다

## Objective
`JOBS.cashDeploy`가 평일 09:30~15:00 30분 간격 12회로 `python -m src.orchestrator
--deploy-cash`를 돌리고, 겹침 가드(`guarded: true`)가 걸린다. `renderPlist`가
평일 5일 × 12회 = **60개**의 `StartCalendarInterval` 항목을 낸다.

## Wiki pages (read these first, only these)
- `wiki/platforms/processes/background-services.md` — launchd 스케줄 잡의 4대 원칙
  (최소 환경/절대경로, 명시적 로그, 슈퍼바이저, 관측으로 검증). 이 저장소의
  `renderPlist`는 이미 넷을 다 지키고 있으니 **깨뜨리지 않는 것**이 목표다.
- `wiki/backend/common/jobs/scheduled-job-overlap.md` — 겹침 방지와 hang guard를
  **짝으로** 걸어야 하는 이유. `guarded: true`가 붙이는 `guardScript`가 이 저장소의
  flock 상당물이고, `STALE_LOCK_MINUTES`(15)가 hang guard다.

## Inputs
- 태스크 10의 산출물: `JobName`에 `"cashDeploy"` 존재
- 기존 파일: `cli/launchd.ts` — `JobSpec`(line 82), `JOBS`(line 119),
  `guardScript`(line 198), `scheduleXml`(line 257), `STALE_LOCK_MINUTES = 15`(line 69)
- **고정 계약 (D19)**: args는 정확히 `["-m", "src.orchestrator", "--deploy-cash"]`,
  로그는 정확히 `"cashDeploy.log"`.
- 바인딩되는 결정: **D8**, **D18**, **D19**

## Steps
1. `cli/launchd.ts`의 `JOBS`에 항목을 추가한다. 위치는 `dipBuy` 바로 뒤
   (`JOB_KEYS` 순서와 맞춘다):
   ```ts
   // 장중 현금 재배치 — 청산으로 회수된 현금을 같은 날 다시 투입한다.
   // 30분 간격이라 앞 실행이 남아 있을 수 있어 guarded: 겹침 시 두 번째는 즉시 exit 0.
   // 파이썬 쪽 cash_deploy.max_candidates_per_run(4)이 한 회 실행을 최악
   // 4 x 180s = 12분으로 묶어, STALE_LOCK_MINUTES(15) 안에 끝나도록 되어 있다.
   cashDeploy: {
     args: ["-m", "src.orchestrator", "--deploy-cash"],
     schedule: { times: CASH_DEPLOY_TIMES },
     log: "cashDeploy.log",
     guarded: true,
   },
   ```
2. `JOBS` 정의 **위**에 시각 배열을 상수로 만든다. 12개를 손으로 나열하지 말고
   생성하되, 결과가 리터럴처럼 읽히게 주석으로 범위를 적는다:
   ```ts
   /**
    * 재배치 점검 시각 — 평일 09:30 부터 15:00 까지 30분 간격 12회.
    *
    * 09:05 진입 잡이 끝난 뒤부터, 정규장 종료(15:30) 직전까지. paper 체결은
    * 정규장에만 일어나므로 장 마감 후 시각은 넣지 않는다.
    */
   const CASH_DEPLOY_TIMES = Array.from({ length: 12 }, (_, i) => {
     const minutes = 9 * 60 + 30 + i * 30;
     return { hour: Math.floor(minutes / 60), minute: minutes % 60 };
   });
   ```
   검산: i=0 → 09:30, i=11 → 9*60+30+330 = 900분 = 15:00. 맞다.
3. `scheduleXml` / `renderPlist` / `guardScript`는 **수정하지 않는다**. `times`
   분기와 `guarded` 분기가 이미 존재한다. 새 schedule 타입을 만들지 말 것 (D18).
4. `STALE_LOCK_MINUTES`도 **바꾸지 않는다**. 15분은 시그널 잡 기준으로 정해진 값이고,
   cashDeploy는 파이썬 쪽 후보 상한으로 12분 안에 끝나도록 설계되어 그 아래에 든다.
   이 상수를 잡별로 만들고 싶어지면 멈추고 보고할 것 — 계획 결함이다.
5. `cli/launchd.ts` 상단 모듈 docstring의 `"the five trading jobs"` /
   `"The eight jobs"` 같은 개수 문구를 실제 개수로 고친다.

## Deliverables
- `cli/launchd.ts` (수정)
- `cli/__tests__/launchd.test.ts` (수정 + 케이스 추가)

## Verify
`cli/__tests__/launchd.test.ts`. 기존 `CFG`(line 62 근처)의 `jobs` 레코드에
`cashDeploy: true`를 추가해야 컴파일된다.

1. 회귀: 기존 `assert.deepEqual([...Object.keys(JOBS)].sort(), [...JOB_KEYS].sort())`
   (line 142)가 통과한다 — 두 인벤토리가 다시 일치.
2. 정상 — 스케줄 개수: `renderPlist("cashDeploy", CFG, HOME_STR, "tester")`의
   `<key>Weekday</key>` 출현 횟수가 **60**이다 (평일 5 × 12회). 기존
   `signalUs` 테스트(line 713, "5 x 2 = 10")와 같은 헬퍼(line 699)를 재사용한다.
3. 정상 — 경계 시각: 같은 plist 문자열에 `09`/`30` 조합과 `15`/`0` 조합이 들어 있다.
   첫 시각과 마지막 시각이 의도대로인지 (오프바이원 방지). 기존 테스트가 시/분을
   어떤 XML 형태로 단언하는지 먼저 읽고 같은 방식으로 쓸 것.
4. 정상 — 배열 자체: `CASH_DEPLOY_TIMES`를 export하지 말고, plist 문자열로만 검증한다
   (구현 세부가 아니라 산출물을 단언).
5. **guarded 검증**: `renderPlist("cashDeploy", ...)`의 `ProgramArguments`가
   `/bin/sh` + `-c`로 시작한다 (guarded 경로). 기존 line 234의
   `for (const job of ["signalKr", "signalUs"] as const)` 루프에 `"cashDeploy"`를
   더하는 것이 가장 자연스럽다.
6. **고정 계약 (D19)**: plist 문자열에 `--deploy-cash`가 들어 있고,
   `JOBS.cashDeploy.log === "cashDeploy.log"`이며, stdout 경로가
   `<home>/logs/cashDeploy.log`, stderr가 `<home>/logs/cashDeploy.err.log`다.
7. 경계 — 락 경로: `lockPath("cashDeploy", HOME_STR)`가
   `<home>/locks/cashDeploy.lock`이고, guard 스크립트 문자열에 그 경로가 들어 있다.
8. 경계 — 비활성: `CFG.jobs.cashDeploy = false`인 config로 `install-jobs` 경로를
   도는 기존 테스트(line 326/399 근처의 `for (const key of JOB_KEYS)` 루프)가
   여전히 통과한다 — 끌 수 있어야 한다.

명령: `cd <worktree> && npm test 2>&1 | tail -20` → `fail 0`, 총 건수가 365보다 커진다.
`npx tsc -p tsconfig.json --noEmit` 도 통과해야 한다 (타입 에러 0).

## Out of scope
- 실제 `launchctl load` 실행 — 테스트는 plist 문자열까지만 본다 (기존 관례).
- 파이썬 쪽 `--deploy-cash` 구현 — 태스크 05·06이 별도 세션에서 한다. 이 태스크는
  **문자열 계약만** 소비한다. 파이썬 파일을 열지 말 것.
- `STALE_LOCK_MINUTES`를 잡별로 만드는 것 — 금지 (Steps §4).
