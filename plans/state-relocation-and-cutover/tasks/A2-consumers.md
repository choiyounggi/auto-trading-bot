# Task A2: point the CLI's callers at the state root

## Objective
`init` asks for and stores `stateDir`, `doctor` checks the new locations, and
`start`/`logs` resolve through them — so the whole CLI compiles and runs against
the contract task A1 established.

## Wiki pages (read these first, only these)
- wiki/infrastructure/config/environment-config.md — use for: rule 5, a required
  key is *asked for*, never guessed, and rule 3, validate before saving.
- wiki/platforms/processes/background-services.md — use for: rule 4, verify by
  observing — `doctor`'s job checks must keep reading `launchctl list`.

## Inputs
- From task A1 (already merged when this runs):
  - `cli/config.ts`: `Config.stateDir` (required), `defaultSignalDir(stateDir)`,
    `JOB_KEYS` (8, incl. `telegramAgent`), `configHome(env?)`
  - `cli/bootstrap.ts`: `venvPython(stateDir)`, `bootstrapPython(cfg, opts)`,
    `isBootstrapped(cfg)`
  - `cli/launchd.ts`: `JOBS` (8), `jobStatus`, `installJob`
- `cli/setup.ts` — `runInit(opts)`, currently 8 steps
- `cli/doctor.ts` — `runDoctor(deps?)`, checks `venv`, `database`, `signal-dir`,
  `kis-api`, per-job
- `cli/index.ts` — `PKG_ROOT`, `runInit({home, projectDir})`, `cmdStart`,
  `cmdLogs`
- Decisions that bind you: D1, D2, D4.

## Steps
1. `cli/index.ts`: pass `stateDir` through. `PKG_ROOT` stays the `projectDir`
   (the code really is there); the state root is `configHome()`. `cmdStart` runs
   `venvPython(cfg.stateDir)`; `cmdLogs` already reads `configHome()/logs` —
   confirm rather than change.
2. `cli/setup.ts`: add a `stateDir` prompt in the existing paths step, defaulting
   to `configHome()` via `askDefault`, validated with `validators.absolutePath`.
   Set `cfg.stateDir` before `parseConfig`, and use
   `defaultSignalDir(stateDir)` for the signal-dir suggestion. The launchd step
   already loops `JOB_KEYS`, so `telegramAgent` appears automatically — confirm,
   and include its "always on" nature in the prompt text rather than a schedule.
3. `cli/doctor.ts`: `venv` and `database` checks read `cfg.stateDir`. Add a
   `state-dir` check: absent → `fail` with hint `run: kis-trader init`; present
   but **inside** `cfg.projectDir` → `fail` with a hint naming the upgrade
   hazard, since that is exactly the configuration this whole phase exists to
   prevent. Otherwise `pass`.
4. Update the three test files.

## Deliverables
- `cli/index.ts`, `cli/setup.ts`, `cli/doctor.ts`
- `cli/__tests__/cli.test.ts`, `cli/__tests__/setup.test.ts`,
  `cli/__tests__/doctor.test.ts`

## Verify
`npm test` green **and `npm run build` exits 0** (A1 left the tree
uncompilable on purpose; this task closes it), with at least:
- normal: `runInit` saves a config whose `stateDir` is the answered value and
  whose `signalDir` defaults to `<stateDir>/data/signals`.
- normal: the `stateDir` prompt's default is `configHome()`.
- normal: `doctor`'s `venv` check reads `<stateDir>/.venv/bin/python` — assert
  the probed path.
- error: `doctor` reports `state-dir` as **fail** when `stateDir` is inside
  `projectDir`, and the hint mentions the upgrade hazard.
- error: an invalid config is still never saved.
- boundary: `telegramAgent` is offered by the job loop and lands in `cfg.jobs`.
- boundary: the existing secret-leak assertion still holds over the new prompt.
- boundary: `node dist/index.js help` exits 0 after the build.

## Out of scope
- The `upgrade` command — task A3.
- README — task A4.
