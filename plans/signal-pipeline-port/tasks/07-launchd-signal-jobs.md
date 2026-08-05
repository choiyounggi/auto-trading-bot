# Task 07: add the two signal jobs, with multi-time schedules and a run bound

## Objective
`JOBS` carries seven entries — the five trading jobs plus `signalKr` (weekdays
16:30) and `signalUs` (weekdays 22:35 **and** 23:35) — and every signal run is
bounded by a timeout and guarded against overlapping itself.

## Wiki pages (read these first, only these)
- wiki/backend/common/jobs/scheduled-job-overlap.md — use for: rule 1 (assume
  overlap), the "Single host, cron" row (`flock -n`), and rule 3 (pair
  skip-on-overlap with a hang guard — `timeout`, or `Forbid` blocks forever).
- wiki/platforms/processes/background-services.md — use for: rule 1 (minimal
  environment: absolute paths, env declared in the plist), rule 2 (explicit log
  paths), rule 4 (verify by observing `launchctl list`).

## Inputs
- `cli/launchd.ts` — exports `JobKey` (= `JobName` from config), `JobSpec`,
  `JOBS`, `renderPlist`, `installJob`, `uninstallJob`, `jobStatus`, `labelFor`,
  `plistPath`, `LaunchctlRunner`
- `cli/config.ts` — task 06 has ALREADY widened `JOB_KEYS` to 7 entries
  (`orchestrator, monitor, reconciler, dipBuy, usOrchestrator, signalKr, signalUs`).
  **Import and consume it; do not edit `cli/config.ts` in this task** — task 06
  owns that file.
- `cli/__tests__/launchd.test.ts` — extend it
- The schedules being preserved (measured from the running installation):
  | key | module | schedule |
  |-----|--------|----------|
  | `signalKr` | `-m src.signal.main --lookback 260` | weekdays 16:30 |
  | `signalUs` | `-m src.signal.main --overseas` | weekdays 22:35 and 23:35 |
  **Confirm the US flag** by reading `~/stock-signal-bot/scripts/run_us_open.sh`
  and `src/signal/main.py`'s argparse before writing it; if the real flag differs,
  use the real one and say so in the task report.
- Decisions that bind you: D7 (timeout + lock), D8 (multi-time schedules).

## Steps
1. Confirm `JOB_KEYS` already carries the 7 entries (task 06 did this). If it
   does not, that is a plan/seam defect — report BLOCKED rather than editing
   `cli/config.ts` yourself, because another task owns it.
2. Extend `JobSpec.schedule` to accept a third form:
   `{ times: { hour: number; minute: number }[] }`, alongside the existing
   `{ hour, minute }` and `{ intervalSec }`. In `renderPlist`, emit one
   `StartCalendarInterval` dict per (weekday 1–5 × time) — `signalUs` therefore
   produces **10** dicts. The two existing forms must render byte-identically to
   before.
3. Bound and serialise every signal run (D7). Build the signal jobs'
   `ProgramArguments` as:
   `["/bin/sh","-c","/usr/bin/flock -n <lock> /usr/bin/timeout 900 <python> -m src.signal.main <args>"]`
   with `<lock>` = `join(home, "locks", "<job>.lock")` and `<python>` =
   `cfg.pythonPath`. `installJob` must `mkdirSync` the `locks` directory the way
   it already does for `logs`. Use absolute paths for `flock`, `timeout` and the
   interpreter — launchd supplies no useful PATH.
   **Verify `/usr/bin/flock` and `/usr/bin/timeout` exist on this machine before
   relying on them**; macOS ships neither by default. If they are absent, say so
   in the task report and instead implement the lock with a plain
   `mkdir`-based guard inside a small `sh -c` expression and drop `timeout` in
   favour of launchd's own `ExitTimeOut`. Do not invent a third option.
4. Trading jobs keep their current `ProgramArguments` shape — do not wrap them.
5. Extend `cli/__tests__/launchd.test.ts`.

## Deliverables
- `cli/launchd.ts` (modified)
- `cli/__tests__/launchd.test.ts` (modified)

## Verify
- `npm test` green, with at least:
  - normal: `renderPlist("signalKr", ...)` contains exactly **5**
    `<key>Weekday</key>` entries and `<integer>16</integer>` / `<integer>30</integer>`.
  - normal: `renderPlist("signalUs", ...)` contains exactly **10**
    `<key>Weekday</key>` entries, covering both 22:35 and 23:35.
  - normal: `renderPlist("orchestrator", ...)` is unchanged from before this task
    — assert the 5-dict single-time form still renders (regression guard on step 2).
  - normal: `renderPlist("monitor", ...)` still uses `StartInterval` and no
    calendar schedule.
  - normal: a signal job's `ProgramArguments` contains the lock path, the timeout
    bound, and `cfg.pythonPath` — assert the interpreter is the absolute
    configured path, never a bare `python3`.
  - error: `Object.keys(JOBS).sort()` deep-equals `[...JOB_KEYS].sort()` — the
    7-vs-7 guard that keeps the table and the config inventory from diverging.
  - boundary: `JOB_KEYS.length === 7` (read-only assertion — task 06 set it).
  - boundary: every rendered job still passes `plutil -lint` (the existing test
    already does this — extend it over the two new jobs).
- Paste the result of your `/usr/bin/flock` and `/usr/bin/timeout` existence check.

## Out of scope
- `cli/config.ts` and its test — task 06 owns them, including the `JOB_KEYS`
  widening this task depends on.
- Installing the jobs during onboarding — task 08 reads `cfg.jobs`.
- Reporting their status — task 09.
