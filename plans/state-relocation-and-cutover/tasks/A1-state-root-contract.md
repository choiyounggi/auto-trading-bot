# Task A1: give the config a state root, and move venv/DB/jobs onto it

## Objective
`Config` carries a required `stateDir`; the venv, SQLite database and job
working paths all resolve from it instead of from the installed package
directory; and `telegramAgent` joins the job inventory as a KeepAlive daemon.

## Wiki pages (read these first, only these)
- wiki/infrastructure/config/environment-config.md — use for: rule 2 (a named
  config value read through one code path), rule 4 (the schema IS the key
  inventory), rule 5 (a required key gets no default in the parser).
- wiki/platforms/processes/background-services.md — use for: the macOS
  LaunchAgent row and rule 3 (supervisor restart via `KeepAlive`) — this is what
  the new `telegramAgent` job must render.
- wiki/security/dependencies/supply-chain.md — use for: rule 1, reproducible
  installs — the editable install is what keeps the venv usable after the
  package directory is replaced.

## Inputs
- `cli/config.ts` — `Config`, `parseConfig`, `REQUIRED`, `JOB_KEYS` (7),
  `JobName`, `defaultSignalDir(projectDir)`, `configHome(env?)`
- `cli/launchd.ts` — `JobSpec`, `JOBS`, `renderPlist`, `installJob`,
  `guardScript`, `lockPath`, `JobKey`
- `cli/bootstrap.ts` — `venvPython(projectDir)`, `bootstrapPython(cfg, opts)`,
  `isBootstrapped(cfg)`, `SQLITE3`
- The daemon being adopted: `~/Library/LaunchAgents/com.choeyeonggi.telegramagent.plist`
  runs `<venv>/bin/python -m src.agent.telegram_agent` with `RunAtLoad`,
  `KeepAlive`, `ThrottleInterval 30`. Read it for the exact keys.
- Decisions that bind you: D1 (`stateDir`), D2 (venv at `<stateDir>/.venv`,
  editable install of `projectDir`), D4 (data/logs/signals under `stateDir`),
  D5 (`telegramAgent`), D6 (these three files are one task).

## Why all three files are one task
`JOB_KEYS` types both `Config.jobs` (`Record<JobName, boolean>`) and `JOBS`
(`Record<JobKey, JobSpec>`), and `venvPython`'s argument changes meaning in the
same edit. Splitting them leaves a tree that cannot compile, so no split part is
independently verifiable.

## Steps
1. `cli/config.ts`:
   - Add `stateDir: string` to `Config` and to `REQUIRED`. It is **required with
     no parser default** — the same absolute-path validation the other path keys
     get. `configHome()` supplies the *prompt's* suggestion (task A2), not a
     parser fallback.
   - Change `defaultSignalDir(projectDir)` to `defaultSignalDir(stateDir)`
     returning `join(stateDir, "data", "signals")`. Keep the name; only the
     argument's meaning changes, and its one caller is updated in A2.
   - Widen `JOB_KEYS` to eight by appending `"telegramAgent"`; default it `true`
     alongside the others.
2. `cli/bootstrap.ts`:
   - `venvPython(stateDir)` → `join(stateDir, ".venv", "bin", "python")`.
   - `bootstrapPython` creates the venv at `<cfg.stateDir>/.venv` using
     `cfg.pythonPath`, then installs **editable from the code**:
     `<venvPython> -m pip install -e <cfg.projectDir>[dev] -q` with
     `cwd: cfg.stateDir`. This is the pairing measured to survive a package
     replacement.
   - Migrations apply to `<cfg.stateDir>/data/trades.sqlite`; create
     `<cfg.stateDir>/data` first. The `.sql` files still come from
     `<cfg.projectDir>/data/migrations` — they ship with the code.
   - `isBootstrapped(cfg)` checks `venvPython(cfg.stateDir)` is executable **and**
     `<cfg.stateDir>/data/trades.sqlite` exists.
3. `cli/launchd.ts`:
   - `JobSpec.schedule` gains `{ keepAlive: true }`. `renderPlist` emits
     `<key>RunAtLoad</key><true/>`, `<key>KeepAlive</key><true/>` and
     `<key>ThrottleInterval</key><integer>30</integer>` for that form, and **no**
     `StartCalendarInterval` or `StartInterval`. The existing three forms render
     byte-identically to before.
   - Add `telegramAgent`: args `["-m", "src.agent.telegram_agent"]`,
     schedule `{ keepAlive: true }`, log `telegramAgent.log`, **not** `guarded`
     (a daemon holding a lock forever is the opposite of what the guard is for).
   - `WorkingDirectory` becomes `cfg.projectDir` still — the code lives there and
     `python -m src.x` needs it on `sys.path` — but every *written* path
     (`StandardOutPath`, `StandardErrorPath`, the lock dir) already derives from
     `home`; confirm none derive from `projectDir`.
   - The plist's `ProgramArguments` interpreter becomes `venvPython(cfg.stateDir)`
     rather than `cfg.pythonPath`: the jobs must run inside the venv that has the
     dependencies, not the bare interpreter. Check what it uses today and change
     it if it is `cfg.pythonPath`.
4. Update the three test files for the widened key set and the new signatures.

## Deliverables
- `cli/config.ts`, `cli/launchd.ts`, `cli/bootstrap.ts`
- `cli/__tests__/config.test.ts`, `cli/__tests__/launchd.test.ts`,
  `cli/__tests__/bootstrap.test.ts`

*(Bound waiver: six files. The `Record<>` + `venvPython` seams make them one
compile unit; see "Why all three files are one task".)*

## Verify
`npm test` green, with at least:
- normal: `parseConfig` accepts a config with `stateDir` and rejects one without
  it — assert the exact message `"stateDir is required"`.
- normal: `defaultSignalDir("/s")` === `"/s/data/signals"`.
- normal: `venvPython("/s")` === `"/s/.venv/bin/python"`.
- normal: `bootstrapPython` with a stub runner creates the venv under
  `cfg.stateDir` and runs `pip install -e <cfg.projectDir>` — assert the
  **editable target is projectDir while the venv path is stateDir**, since that
  split is the whole point of the task.
- normal: `renderPlist("telegramAgent", …)` contains `RunAtLoad`, `KeepAlive`,
  `ThrottleInterval` 30, and **neither** `StartCalendarInterval` **nor**
  `StartInterval`.
- normal: the interpreter in every rendered `ProgramArguments` is
  `venvPython(cfg.stateDir)` — assert it, because a job that runs the bare
  interpreter has none of the Python dependencies.
- error: `stateDir: "relative/path"` → `"stateDir must be an absolute path"`.
- error: `Object.keys(JOBS).sort()` deep-equals `[...JOB_KEYS].sort()` — the
  8-vs-8 guard.
- boundary: `JOB_KEYS.length === 8` and contains `"telegramAgent"`.
- boundary: `renderPlist` for the three pre-existing schedule forms
  (`{hour,minute}`, `{times}`, `{intervalSec}`) is unchanged — regression guard.
- boundary: `telegramAgent` is **not** wrapped in the `/bin/sh` guard; the two
  signal jobs still are.
- boundary: every rendered plist still passes `plutil -lint`.
- boundary: `isBootstrapped` is false when the venv exists but the DB does not,
  and vice versa.

## Out of scope
- `cli/setup.ts`, `cli/doctor.ts`, `cli/index.ts` — task A2 updates the callers.
  They will not compile until A2 runs; that is expected and is why A2 is the
  next wave rather than a parallel task.
- The `upgrade` command's re-bootstrap — task A3.
