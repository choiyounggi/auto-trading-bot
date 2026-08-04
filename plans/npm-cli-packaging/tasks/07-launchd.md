# Task 07: launchd job rendering, install, uninstall, status

## Objective
The five trading jobs are rendered to plists at runtime from the user's config
(no committed author paths), installed under `~/Library/LaunchAgents`, verified
by observing `launchctl list`, and removable again.

## Wiki pages (read these first, only these)
- wiki/platforms/processes/background-services.md — use for: the macOS rows of
  the mechanism table (LaunchAgent + `StartCalendarInterval`), and all four
  follow-up rules: (1) minimal environment → absolute paths + env in the plist,
  (2) explicit `StandardOutPath`/`StandardErrorPath`, (3) supervisor restart,
  (4) **verify by observing `launchctl list`, not by the launch exit code**;
  plus the edge-case row about version-manager-installed binaries.
- wiki/platforms/environment/path-resolution.md — use for: the "cron / launchd /
  systemd services" row (daemon-spawned jobs get a minimal PATH and no rc files).

## Inputs
- `cli/config.ts` from tasks 03 + 03b — `Config` type (`projectDir`,
  `pythonPath`, `signalDir`, `mode`, `jobs`), plus `JOB_KEYS` / `JobName`,
  which task 03b has already widened to five entries including `usOrchestrator`.
- The schedules to preserve, read off the currently-installed jobs:
  | job key | module invocation | schedule |
  |---------|-------------------|----------|
  | `orchestrator` | `-m src.orchestrator --carry-over` | weekdays 09:05 |
  | `dipBuy` | `-m src.orchestrator --dip-only` | weekdays 15:00 |
  | `reconciler` | `-m src.reconciler` | weekdays 16:00 |
  | `monitor` | `-m src.monitor` | every 300 s (`StartInterval`) |
  | `usOrchestrator` | `-m src.orchestrator --asset-class overseas_stock` | weekdays 22:45 |
- Decisions that bind you: D9 (runtime rendering, label scheme, env contents,
  observe-to-verify), D12 (`KIS_TRADER_SIGNAL_DIR` goes into the plist env).

## Steps
1. Create `cli/launchd.ts`.
2. **Do not declare a second job-name union.** Import the one that already
   exists: `import { JOB_KEYS, type JobName } from "./config.js"` and
   `export type JobKey = JobName;` (re-exported so callers may use either name).
   Task 03b has already widened `JOB_KEYS` to the five entries in the table
   above; a locally-declared union would silently drift from `Config.jobs`.
   Then `export const JOBS: Record<JobKey, { args: string[]; schedule: { hour: number; minute: number } | { intervalSec: number }; log: string }>`
   built from the table above. `log` is the basename, e.g. `orchestrator.log`.
   Add a test asserting `Object.keys(JOBS).sort()` deep-equals
   `[...JOB_KEYS].sort()`, so the table and the config inventory cannot diverge.
3. `export function labelFor(job: JobKey, username = userInfo().username): string`
   → `com.<username>.kistrader.<job>`.
4. `export function plistPath(label: string): string` →
   `join(homedir(), "Library", "LaunchAgents", label + ".plist")`.
5. `export function renderPlist(job: JobKey, cfg: Config, home: string, username?: string): string`
   - `ProgramArguments`: `[cfg.pythonPath, ...JOBS[job].args]` — absolute
     interpreter path, never a bare `python3`.
   - `WorkingDirectory`: `cfg.projectDir`.
   - `EnvironmentVariables`: `PATH` =
     `<dirname(cfg.pythonPath)>:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`
     (deduped, order preserved), `HOME` = `homedir()`,
     `KIS_TRADER_HOME` = `home`, `KIS_MODE` = `cfg.mode`,
     `KIS_TRADER_SIGNAL_DIR` = `cfg.signalDir`.
   - Schedule: `StartCalendarInterval` as an **array of five dicts**, one per
     `Weekday` 1–5, each with the job's `Hour`/`Minute`; or `StartInterval`
     when the job defines `intervalSec`.
   - `StandardOutPath` / `StandardErrorPath`:
     `join(home, "logs", JOBS[job].log)` and `...err`.
   - No `KeepAlive` on the scheduled jobs (they are one-shot runs; KeepAlive
     would restart them in a loop). `monitor` also gets none — `StartInterval`
     already re-runs it.
   - Escape `&`, `<`, `>` in every interpolated string.
6. `export function installJob(job, cfg, home, run = defaultRun): { label: string; path: string; loaded: boolean; message: string }`
   - `mkdirSync` the LaunchAgents dir and `join(home,"logs")`.
   - Write the plist at mode 0644.
   - `launchctl bootout gui/<uid>/<label>` (ignore failure — not loaded yet),
     then `launchctl bootstrap gui/<uid> <path>`. Retry the bootstrap up to 3
     times with a 700 ms pause when a previous bootout happened, because the
     service slot is not free the instant bootout returns.
   - **Then confirm by observation**: run `launchctl list` and set
     `loaded: true` only if the label appears in its output. Do not infer
     `loaded` from the bootstrap exit code.
7. `export function uninstallJob(job, run = defaultRun): { label: string; path: string; removed: boolean; message: string }`
   — bootout, then unlink the plist when present.
8. `export function jobStatus(job, run = defaultRun): "loaded" | "installed-not-loaded" | "absent"`
   — `absent` when no plist file; otherwise `loaded` iff the label is in
   `launchctl list` output.
9. Implement the 700 ms pause with `Atomics.wait` on an `Int32Array` over a
   `SharedArrayBuffer`, matching the reference; do **not** use a busy spin.
10. Create `cli/__tests__/launchd.test.ts`. Every `launchctl` call goes through
    the injected `run`; tests must not install anything.

## Deliverables
- `cli/launchd.ts`
- `cli/__tests__/launchd.test.ts`

## Verify
- `npm test` passes with at least these cases:
  - normal: `renderPlist("orchestrator", cfg, "/tmp/h", "alice")` output contains
    `<string>/abs/python3.11</string>`, `--carry-over`,
    `<key>KIS_TRADER_SIGNAL_DIR</key>` with the config value, and exactly five
    `<key>Weekday</key>` entries.
  - normal: `renderPlist("monitor", ...)` contains
    `<key>StartInterval</key>` + `<integer>300</integer>` and **no**
    `StartCalendarInterval`.
  - normal: `labelFor("dipBuy","alice")` === `"com.alice.kistrader.dipBuy"`.
  - error: `installJob` where the stub `launchctl list` output does **not**
    contain the label returns `loaded: false` even though the stubbed
    bootstrap exited 0 — this is the observe-don't-trust-exit-code rule.
  - error: `installJob` whose bootstrap stub always throws returns
    `loaded:false` with a message naming the failure, and does not throw.
  - boundary: a `projectDir` containing `&` is escaped to `&amp;` in the output
    and the plist still parses — assert by piping the string through
    `plutil -lint -` via the test's own child process, or by asserting the
    literal `&amp;` is present and a bare `&` is not.
  - boundary: `jobStatus` returns `"absent"` when the plist file does not exist,
    and `"installed-not-loaded"` when the file exists but the label is missing
    from `launchctl list`.

## Out of scope
- Deciding *which* jobs to install (task 10 reads `cfg.jobs`).
- Deleting the eight committed author plists (task 15).
