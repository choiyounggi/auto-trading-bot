# Task 06: the job inventory and the job table, together

## Objective
`cli/config.ts` knows seven jobs and the news backend; `cli/launchd.ts` renders
all seven, including the two signal jobs with their multi-time schedules and an
overlap/hang guard that needs no non-stock binary.

## Why 06 and 07 are one task
They were planned as two. The type system disproved that: `JOB_KEYS` types both
`Config.jobs` (`Record<JobName, boolean>`) and `JOBS` (`Record<JobKey, JobSpec>`),
so widening the key set in `config.ts` makes `launchd.ts` and four test files fail
to compile *in the same commit*. Task 06's own Verify (`npm test` green) was
unsatisfiable while `launchd.ts` sat outside its scope. One contract, one task.
(Plan repair, recorded during execution — third instance this run of "a change
that necessarily breaks files the task may not touch".)

## Wiki pages (read these first, only these)
- wiki/backend/common/jobs/scheduled-job-overlap.md — use for: rule 1 (assume the
  scheduler starts overlapping runs), the single-host lock row, and **rule 3**
  (pair skip-on-overlap with a hang guard, or one stuck run blocks the schedule
  forever).
- wiki/platforms/processes/background-services.md — use for: rule 1 (minimal
  environment — absolute paths, env declared in the plist), rule 2 (explicit log
  paths), rule 4 (verify by observing `launchctl list`).
- wiki/infrastructure/config/environment-config.md — use for: rule 2 (a named
  value per behaviour), rule 4 (the schema is the key inventory), rule 5
  (required keys get no default).
- wiki/backend/node/boundaries/runtime-validation.md — use for: rules 2–3, the
  parser stays the single source of truth and the type derives from it.

## Inputs
- `cli/config.ts` — `MODES`, `AGENTS`, `JOB_KEYS` (5 today), `JobName`, `Config`,
  `ParseResult`, `parseConfig`, `loadConfig`, `saveConfig`, `configHome`.
  `llmAgent`'s handling is the pattern `newsLlmBackend` must copy.
- `cli/launchd.ts` — `JobKey` (= `JobName`), `JobSpec`, `JOBS`, `renderPlist`,
  `installJob`, `uninstallJob`, `jobStatus`, `labelFor`, `plistPath`,
  `LaunchctlRunner`.
- `cli/__tests__/config.test.ts` — **already carries this task's new tests**; a
  previous session wrote 142 lines of them before being interrupted. Read them
  first and make them pass rather than rewriting them.
- Four files hold `jobs` object literals that will stop compiling when the key
  set widens; each needs `signalKr` and `signalUs` added:
  `cli/__tests__/bootstrap.test.ts:67`, `cli/__tests__/doctor.test.ts:41`,
  `cli/__tests__/doctor.test.ts:610`, `cli/__tests__/launchd.test.ts:50`.
- Decisions that bind you: D5, D7, D8, D9.

## Measured environment facts — do not re-derive these
- `/usr/bin/flock` **does not exist** on macOS. `/usr/bin/timeout` **does not
  exist**. `/opt/homebrew/bin/timeout` exists only because Homebrew coreutils is
  installed here — this package ships to arbitrary Macs, so depending on it would
  break for users without Homebrew. **Use neither.**
- The signal schedules being preserved, read off the running installation:
  KR weekdays **16:30**; US weekdays **22:35 and 23:35**.
- Existing trading schedules that must keep their gap: orchestrator 16:45,
  usOrchestrator 22:45. Signal must finish before them.

## Steps
1. `cli/config.ts`:
   - `export const NEWS_BACKENDS = ["none","claude","codex","pi"] as const;`
     and `export type NewsBackend = (typeof NEWS_BACKENDS)[number];`
     Deliberately excludes `gemini` (the signal code does not support it) and
     includes `none` (the default — zero LLM cost).
   - Add `newsLlmBackend: NewsBackend` to `Config`; in `parseConfig` mirror
     `llmAgent`: absent → `"none"`; invalid → error
     `` `newsLlmBackend must be one of ${NEWS_BACKENDS.join(", ")}` ``.
     **Do not derive it from `llmAgent`** (D9).
   - Widen `JOB_KEYS` to
     `["orchestrator","monitor","reconciler","dipBuy","usOrchestrator","signalKr","signalUs"]`
     and default both new jobs to `true`.
   - `export function defaultSignalDir(projectDir: string): string` returning
     `join(projectDir, "data", "signals")`. `signalDir` itself stays **required
     with no default**.
2. `cli/launchd.ts`:
   - Extend `JobSpec.schedule` with a third form `{ times: {hour,minute}[] }`.
     `renderPlist` emits one `StartCalendarInterval` dict per (weekday 1–5 × time)
     — `signalUs` yields **10**. The existing single-time and `intervalSec` forms
     must render byte-identically to today.
   - Add the two entries:
     `signalKr` → `["-m","src.signal.main","--lookback","260"]`, weekdays 16:30,
     log `signalKr.log`; `signalUs` → the overseas invocation, `times`
     `[{22,35},{23,35})]`, log `signalUs.log`.
     **Confirm the overseas flag** by reading `src/signal/main.py`'s argparse and
     `~/stock-signal-bot/scripts/run_us_open.sh`; use the real flag and state it
     in your report.
   - Wrap **only the two signal jobs** in this guard (D7). Trading jobs keep their
     current shape.
     ```
     ProgramArguments = ["/bin/sh","-c", GUARD]
     ```
     where `GUARD` is, with `L` = `join(home,"locks","<job>.lock")` and `PY` =
     `cfg.pythonPath`:
     ```sh
     L=<lock>; if ! mkdir "$L" 2>/dev/null; then
       if [ -n "$(find "$L" -maxdepth 0 -mmin +15 2>/dev/null)" ]; then
         rmdir "$L" 2>/dev/null; mkdir "$L" 2>/dev/null || exit 0;
       else exit 0; fi
     fi
     trap 'rmdir "$L" 2>/dev/null' EXIT
     exec <PY> -m src.signal.main <args>
     ```
     Why this shape: `mkdir` is atomic on POSIX, so it is the lock; a second run
     exits 0 immediately (skip-on-overlap, wiki rule 1). The 15-minute staleness
     reclaim is the hang guard (wiki rule 3) — without it one crashed run leaves
     the lock forever and every later schedule silently skips. `find -mmin` is
     BSD-compatible. Nothing here needs a non-stock binary.
   - `installJob` must `mkdirSync` the `locks` directory as it already does `logs`.
3. Add `signalKr`/`signalUs` to the four `jobs` literals listed under Inputs.
   Change nothing else in those files.
4. Extend `cli/__tests__/config.test.ts` (build on what is already there) and
   `cli/__tests__/launchd.test.ts`.

## Deliverables
- `cli/config.ts`, `cli/launchd.ts`
- `cli/__tests__/config.test.ts`, `cli/__tests__/launchd.test.ts`
- `cli/__tests__/bootstrap.test.ts`, `cli/__tests__/doctor.test.ts` (the `jobs`
  literals only)

*(Bound waiver: six files, over the plan's ≤3 rule. The `Record<>` contract makes
the first four inseparable, and the last two are one-line literal fixes that the
same compile requires.)*

## Verify
`npm test` green, with at least:
- normal: `renderPlist("signalKr", ...)` has exactly **5** `<key>Weekday</key>`
  entries with hour 16 / minute 30.
- normal: `renderPlist("signalUs", ...)` has exactly **10**, covering 22:35 and 23:35.
- normal: `renderPlist("orchestrator", ...)` and `renderPlist("monitor", ...)`
  are unchanged from before this task — the single-time and `intervalSec` forms
  are regression-guarded.
- normal: a signal job's `ProgramArguments` is `/bin/sh -c ...` containing the
  lock path, the `-mmin +15` reclaim, the `trap`, and `cfg.pythonPath` as an
  absolute path (never a bare `python3`).
- normal: the guard string contains **neither** `flock` **nor** `timeout` —
  assert both absences explicitly; they do not exist on stock macOS.
- normal: `defaultSignalDir("/opt/kis")` === `"/opt/kis/data/signals"`.
- error: `Object.keys(JOBS).sort()` deep-equals `[...JOB_KEYS].sort()` — the
  7-vs-7 guard.
- error: `newsLlmBackend: "gemini"` → `deepEqual` on the error array gives exactly
  `["newsLlmBackend must be one of none, claude, codex, pi"]`.
- error: `jobs: { signalKr: "yes" }` → exactly `"jobs.signalKr must be a boolean"`.
- boundary: `JOB_KEYS.length === 7`; omitting `jobs` defaults all seven to `true`.
- boundary: `llmAgent: "gemini"` with no `newsLlmBackend` still yields `"none"`.
- boundary: `signalDir` still required — omitting it errors.
- boundary: every rendered job still passes `plutil -lint` (extend the existing test).

## Out of scope
- `cli/setup.ts` (task 08) and `cli/doctor.ts`'s check logic (task 09) — you touch
  `doctor.test.ts` only to add two keys to its `jobs` literals.
- `src/**`.
