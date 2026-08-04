# Task 08: Python venv + dependency + SQLite bootstrap

## Objective
`bootstrapPython()` creates `<projectDir>/.venv`, installs the project with dev
extras, and applies every `data/migrations/*.sql` to `data/trades.sqlite` — the
work `scripts/setup.sh` does today, driven from TypeScript with progress
reported per step.

## Wiki pages (read these first, only these)
- wiki/platforms/processes/non-interactive-cli-invocation.md — use for: rule 1
  (`</dev/null` / stdin detached at the call site, because `pip` can prompt),
  rule 3 (bound every call with a timeout), rule 4 (write output to a file so
  "0 bytes" is evidence).
- wiki/platforms/toolchains/version-management.md — use for: invoking the real
  interpreter's absolute path rather than a shim.

## Inputs
- `cli/config.ts` (task 03) — `Config.projectDir`, `Config.pythonPath`.
- Existing `scripts/setup.sh` — the behaviour being ported.
- Existing `data/migrations/` — `0001_init.sql` … `0004_partial_exit.sql`.
- Decisions that bind you: D10 (absolute interpreter path only).

## Steps
1. Create `cli/bootstrap.ts`.
2. `export type StepResult = { step: string; ok: boolean; detail: string };`
3. `export function venvPython(projectDir: string): string` →
   `join(projectDir, ".venv", "bin", "python")`.
4. `export function bootstrapPython(cfg: Config, opts?: { run?: RunFn; onStep?: (r: StepResult) => void }): StepResult[]`
   where `RunFn = (cmd: string, args: string[], timeoutMs: number) => { code: number; out: string }`.
   Default `RunFn` uses `spawnSync` with `stdio: ["ignore","pipe","pipe"]`
   (stdin detached — the wiki's fd-0 rule) and the given `timeout`.
   Steps, in order, each producing one `StepResult` and each short-circuiting
   the rest on failure:
   1. `"venv"` — skip with `ok:true, detail:"already exists"` when
      `venvPython(projectDir)` is executable; otherwise
      `<cfg.pythonPath> -m venv <projectDir>/.venv`, timeout 180 s.
   2. `"pip-upgrade"` — `<venvPython> -m pip install --upgrade pip -q`, timeout 300 s.
   3. `"deps"` — `<venvPython> -m pip install -e .[dev] -q` run with
      `cwd: cfg.projectDir`, timeout 900 s.
   4. `"migrations"` — for each file in `data/migrations/` sorted by name,
      apply it with `/usr/bin/sqlite3 <projectDir>/data/trades.sqlite` reading
      the SQL from the file. Create `<projectDir>/data` first. Applying an
      already-applied migration must not fail the step: treat a non-zero exit
      whose output contains `already exists` or `duplicate column` as `ok:true`
      with `detail:"already applied"`. Any other non-zero exit fails.
   - Call `opts.onStep` after each step so the caller can print live progress.
   - Return the array of all `StepResult`s produced.
5. `export function isBootstrapped(cfg: Config): boolean` — true iff
   `venvPython(cfg.projectDir)` is executable **and**
   `<projectDir>/data/trades.sqlite` exists.
6. Create `cli/__tests__/bootstrap.test.ts` with `run` injected — no real venv
   is ever created by the tests.

## Deliverables
- `cli/bootstrap.ts`
- `cli/__tests__/bootstrap.test.ts`

## Verify
- `npm test` passes with at least these cases:
  - normal: all stubbed commands exit 0 → four `StepResult`s, every `ok:true`,
    and `onStep` was called exactly four times.
  - normal: the first stubbed call receives `cfg.pythonPath` as the command and
    `["-m","venv", ...]` as args — assert the interpreter is the absolute
    configured path, not `"python3"`.
  - error: the `deps` step exiting non-zero yields `ok:false` on that step and
    **no** `migrations` step in the result array (short-circuit proven by length).
  - error: a `migrations` call exiting non-zero with unrelated output
    (`"disk I/O error"`) yields `ok:false`.
  - boundary: a `migrations` call exiting non-zero with output containing
    `"table positions already exists"` yields `ok:true` with
    `detail === "already applied"`.
  - boundary: `bootstrapPython` against a temp `projectDir` whose
    `.venv/bin/python` already exists returns the `venv` step as
    `ok:true, detail:"already exists"` and the stub was **not** invoked for it.
  - boundary: an empty `data/migrations/` directory still returns a
    `migrations` step with `ok:true` (nothing to apply is not a failure).

## Out of scope
- Running pytest (task 16 handles final verification), and prompting the user
  (task 10).
