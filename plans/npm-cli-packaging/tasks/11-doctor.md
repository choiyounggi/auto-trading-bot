# Task 11: `doctor` diagnostics

## Objective
`runDoctor()` returns a list of named checks with pass/warn/fail status and a
remediation hint for every non-pass, and exits non-zero when any check fails.
It never crashes on a machine with no config.

## Wiki pages (read these first, only these)
- wiki/platforms/processes/background-services.md — use for: rule 4, *verify by
  observing* (`launchctl list`), not by a launch command's exit code — the job
  checks must observe actual state.
- wiki/infrastructure/config/environment-config.md — use for: rule 3/4, the
  config schema is the key inventory, so `doctor` reports each required key.

## Inputs
- `cli/config.ts` — `loadConfig`, `configHome` (task 03)
- `cli/keychain.ts` — `keychainHas`, `KIS_SERVICE`, `TELEGRAM_SERVICE`,
  `kisAccount`, `TELEGRAM_TOKEN_ACCOUNT`, `TELEGRAM_CHATID_ACCOUNT` (task 04)
- `cli/resolve-cli-path.ts` — `detectAgents` (task 05)
- `cli/python.ts` — `pythonVersion`, `isSupported` (task 06)
- `cli/launchd.ts` — `jobStatus`, `JobKey`, `JOBS` (task 07)
- `cli/bootstrap.ts` — `venvPython` (task 08)
- `src/broker/probe.py` — the JSON probe contract from task 11b
- Decisions that bind you: D9 (observe `launchctl list`), D12 (signal dir is
  checked for existence *and* freshness), D18 (probe contract; credentials stay
  in the Python process).

## Steps
1. Create `cli/doctor.ts`.
2. `export type CheckStatus = "pass" | "warn" | "fail";`
   `export interface Check { name: string; status: CheckStatus; detail: string; hint?: string }`
3. `export function runDoctor(deps?: Partial<DoctorDeps>): Check[]` producing,
   in this order:
   1. `config` — `loadConfig()`. Missing/invalid → `fail`, detail = the joined
      errors, hint = `"run: kis-trader init"`. **When this fails, return
      immediately with just this one check** — every later check needs the config.
   2. `python` — `pythonVersion(cfg.pythonPath)`; `pass` when `isSupported`,
      `fail` otherwise with hint `"re-run kis-trader init to re-detect Python"`.
   3. `venv` — `venvPython(cfg.projectDir)` executable → `pass`; else `fail`
      with hint `"run: kis-trader init"`.
   4. `database` — `<projectDir>/data/trades.sqlite` exists → `pass`, else `fail`.
   5. `keychain-kis` — all three of appkey/secret/account present for
      `cfg.mode` → `pass`; some present → `fail` naming the missing accounts;
      none → `fail`. Hint `"run: kis-trader init"`.
   6. `keychain-telegram` — both items present → `pass`; neither → **`warn`**
      (Telegram is optional, per task 10 step 5); exactly one → `fail`.
   7. `llm-agent` — `detectAgents()`; `cfg.llmAgent` found → `pass` with its
      path; configured agent missing but another found → `warn`; none found →
      `fail`.
   8. `signal-dir` — directory missing → `fail` with hint naming
      `cfg.signalDir` and stating that signals come from the separate
      stock-signal-bot project. Directory present but empty → `warn`. Present
      with files → read the newest file's mtime: `pass` when under 72 h old,
      `warn` when older, with the age in the detail.
   9. `kis-api` — live connectivity, delegated to the Python probe from task 11b
      so credentials never enter this process. Run
      `<venvPython(cfg.projectDir)> -m src.broker.probe` with
      `cwd: cfg.projectDir`, `env: { ...process.env, KIS_MODE: cfg.mode }`,
      `stdio: ["ignore","pipe","pipe"]` (stdin detached) and a **15 s timeout**.
      Parse stdout as JSON and map the probe's `reason` field:
      | probe result | check |
      |---|---|
      | `ok: true` | `pass`, detail = `"<mode> account reachable (<cano_masked>)"` |
      | `reason: "missing_credentials"` | `fail`, hint `"run: kis-trader init"` |
      | `reason: "auth_failed"` | `fail`, hint `"app key/secret rejected — re-run kis-trader init"` |
      | `reason: "rate_limited"` | `warn`, detail says the probe was throttled, not that credentials are bad |
      | `reason: "network"` | `warn`, hint `"check connectivity to openapivts.koreainvestment.com"` |
      | anything else, non-zero exit, timeout, or unparseable stdout | `fail`, detail = the first 200 chars of stderr |
      When the `venv` check already failed, emit this check as `warn` with
      detail `"skipped — venv missing"` rather than spawning anything.
   10. one check per `JobKey` named `job:<key>` — `jobStatus`: `loaded` → `pass`;
      `installed-not-loaded` → `warn` with hint
      `"launchctl bootstrap gui/$UID <plist path>"`; `absent` → `warn` when
      `cfg.jobs[key]` is false (deliberately not installed), `fail` when
      `cfg.jobs[key]` is true (should be there and is not).
4. `export function formatChecks(checks: Check[]): string` — one aligned line
   per check: a status glyph (`✔` pass / `!` warn / `✖` fail), the padded name,
   the detail, and the hint on a following indented line when present.
5. `export function exitCodeFor(checks: Check[]): number` — `1` when any check
   is `fail`, else `0`. A `warn` never fails the command.
6. Create `cli/__tests__/doctor.test.ts` with all collaborators stubbed.

## Deliverables
- `cli/doctor.ts`
- `cli/__tests__/doctor.test.ts`

## Verify
- `npm test` passes with at least these cases:
  - normal: all-healthy stubs → every check `pass` and `exitCodeFor` is `0`.
  - normal: `formatChecks` output contains `✔` for a pass and the hint text on
    its own line for a warn that has one.
  - error: `loadConfig` returning `ok:false` yields an array of **length 1**
    whose single check is `config`/`fail` — proving the short-circuit, so no
    later check dereferences a missing config.
  - error: two of three KIS keychain items present → `keychain-kis` is `fail`
    and the detail names the missing account (e.g. `paper-secret`).
  - error: a `JobKey` with `cfg.jobs[key] === true` but `jobStatus` `"absent"`
    → that check is `fail` and `exitCodeFor` is `1`.
  - boundary: neither Telegram item present → `keychain-telegram` is `warn`
    (not fail) and `exitCodeFor` is still `0` when nothing else fails.
  - boundary: exactly one Telegram item present → `fail`.
  - boundary: a `signalDir` whose newest file is 71 h old → `pass`; 73 h old →
    `warn`. Assert both, using an injected clock — do not rely on wall time.
  - boundary: an existing but empty `signalDir` → `warn`, not `fail`.
  - normal: a stubbed probe returning `{"ok":true,"cano_masked":"****0180",...}`
    → `kis-api` is `pass` and the detail contains `****0180`.
  - error: a stubbed probe returning `reason:"auth_failed"` → `kis-api` is
    `fail`; a stubbed probe emitting unparseable stdout → `fail`.
  - boundary: `reason:"rate_limited"` and `reason:"network"` each yield `warn`,
    so a throttled or offline probe never reports the credentials as broken.
  - boundary: when the `venv` check failed, `kis-api` is `warn` with detail
    `"skipped — venv missing"` and the spawn stub was **not** called.

## Out of scope
- Implementing the probe itself — task 11b owns `src/broker/probe.py` and its
  JSON contract. This task only spawns it and maps its `reason` field.
- Subcommand dispatch and the `--json` flag (task 12).
