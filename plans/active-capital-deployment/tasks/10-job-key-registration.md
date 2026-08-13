# Task 10: `cashDeploy`를 잡 인벤토리에 등록한다

## Objective
`JOB_KEYS`에 `cashDeploy`가 들어가고 기본 활성(`true`)이다. `parseConfig`가
`jobs.cashDeploy` 불리언을 검증한다. 이 키 하나로 `doctor` / `install-jobs` /
`uninstall-jobs` / `upgrade`의 per-job 루프가 자동으로 새 잡을 다룬다.

## Wiki pages (read these first, only these)
- `wiki/backend/common/api-design/unenforced-declarations.md` — 설정 어휘를 닫힌
  열거표 하나로 유지하고, 그 표에 없는 키가 조용히 통과하지 않게 하는 규칙.
  이 저장소는 `JOB_KEYS`가 바로 그 닫힌 표다.

## Inputs
- 기존 파일: `cli/config.ts` (`JOB_KEYS` line 33, 기본 `jobs` 레코드 line 179,
  `jobs.<name> must be a boolean` 검증 line 189)
- **고정 계약 (D19)**: JobName은 정확히 `cashDeploy`. 다른 철자 금지.
- 바인딩되는 결정: **D19**

## Steps
1. `cli/config.ts`의 `JOB_KEYS` 배열에 `"cashDeploy"`를 추가한다. 위치는
   `"dipBuy"` **바로 뒤** — 둘 다 국내 장중 잡이라 인접시키면 `doctor` 출력과
   `install-jobs` 로그의 순서가 읽기 쉽다.
2. `parseConfig`의 기본 `jobs` 레코드에 `cashDeploy: true`를 같은 위치에 추가한다.
   기본 활성인 이유: 이 기능의 목적 자체가 "현금을 놀리지 않는 것"이고, 꺼진 채
   배포되면 아무것도 달라지지 않는다.
3. `JOB_KEYS`를 도는 검증 루프(`for (const name of JOB_KEYS)`)는 그대로 두면
   새 키를 자동으로 다룬다 — 추가 코드 없음. 이것이 §"닫힌 열거표 하나" 원칙이 이미
   지켜지고 있다는 증거이므로, 별도 분기를 만들지 말 것.
4. `cli/config.ts`의 주석 중 잡 개수를 세는 문구(`여덟 개`, `eight`, `The eight jobs`
   등)를 찾아 아홉으로 고친다. `grep -rn "eight\|여덟" cli/` 로 전부 찾을 것.

## Deliverables
- `cli/config.ts` (수정)
- `cli/__tests__/config.test.ts` (수정 — `allJobs()` 헬퍼와 관련 단언)

## Verify
`cli/__tests__/config.test.ts`:

1. 정상: `allJobs()` 헬퍼(line 18 근처, "여덟 개 잡" 주석)에 `cashDeploy: true`를
   추가하고, 기존 `"omitting llmAgent and jobs applies the documented defaults"`
   테스트가 통과한다 — 기본값이 실제로 아홉 개임을 고정.
2. 정상: `jobs: { cashDeploy: false }`를 주면 `r.value.jobs.cashDeploy === false`이고
   나머지 여덟 개는 `true`. 부분 지정이 다른 잡을 끄지 않는다.
3. 에러: `jobs: { cashDeploy: "yes" }` → 에러 배열이 정확히
   `["jobs.cashDeploy must be a boolean"]`. 기존 형제 테스트들과 같은 형태로 쓴다.
4. 경계: `jobs: {}` → 아홉 개 전부 기본값 `true`.
5. 경계: `jobs: { cashDeploy: undefined }` → 기존 `if (v === undefined) continue`
   경로를 타서 기본값 `true`, 에러 없음.
6. 회귀: `JOB_KEYS.length === 9`를 단언한다. 실수로 키를 지우면 빨개진다.

명령: `cd <worktree> && npm test 2>&1 | tail -20` → `fail 0`. 기존 365건이 유지되고
새 케이스가 더해진다.

## Out of scope
- `JOBS` 스펙(args/schedule/log) — 태스크 11. 이 태스크만 끝난 상태에서는
  `JOBS`에 `cashDeploy` 항목이 없어 **타입 에러가 난다**. 그것이 의도된 설계다
  (`cli/launchd.ts` 주석: "a job added to config.ts without a schedule here is a
  type error rather than a job that silently never installs").
  → **태스크 10과 11은 반드시 연속으로 수행하고, 11까지 끝난 뒤에 커밋한다.**
- `doctor.ts` / `index.ts` — `JOB_KEYS`를 도는 제네릭 루프라 손댈 것이 없다.
  `grep -n "JOB_KEYS" cli/doctor.ts cli/index.ts`로 확인만 하고 수정하지 말 것.
