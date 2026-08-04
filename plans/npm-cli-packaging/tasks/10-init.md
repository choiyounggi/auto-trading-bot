# Task 10: `init` onboarding flow

## Objective
`runInit()` walks a fresh user through 7 steps and leaves behind a valid
`config.json`, the six Keychain items the Python engine reads, a bootstrapped
venv + SQLite DB, and the selected launchd jobs installed.

## Wiki pages (read these first, only these)
- wiki/security/secrets/secrets-in-code.md — use for: rule 1 (secrets go to the
  secret store, never into `config.json`) and the leak-response table, which is
  why a captured secret is never echoed back or written to a log line.
- wiki/infrastructure/config/environment-config.md — use for: rule 5 (required
  keys get no default) — every required key must be *asked for*, not guessed.

## Inputs
- `cli/config.ts` — `Config`, `parseConfig`, `saveConfig`, `configHome` (task 03)
- `cli/keychain.ts` — `keychainSet`, `KeychainLockedError`, `KIS_SERVICE`,
  `TELEGRAM_SERVICE`, `kisAccount`, `TELEGRAM_TOKEN_ACCOUNT`,
  `TELEGRAM_CHATID_ACCOUNT` (task 04)
- `cli/resolve-cli-path.ts` — `detectAgents`, `SupportedCli` (task 05)
- `cli/python.ts` — `findPython` (task 06)
- `cli/launchd.ts` — `installJob`, `JobKey`, `JOBS` (task 07)
- `cli/bootstrap.ts` — `bootstrapPython` (task 08)
- `cli/prompt.ts` — all helpers + `validators` (task 09)
- Decisions that bind you: D5, D6, D8, D12.

## Steps
1. Create `cli/setup.ts` exporting
   `export async function runInit(opts: { home: string; projectDir: string; io?: Io; deps?: Partial<InitDeps> }): Promise<number>`
   returning a process exit code (0 success, 1 aborted/failed).
   `InitDeps` bundles every injected collaborator listed under Inputs so the
   whole flow is testable without touching the machine.
2. Print step headers as `── Step N/7 — <title> ──`. Use a small local
   `section/ok/warn/fail` writer set that writes to `io.output`.
3. Step 1/7 — **Trading mode.** `choose("Trading mode", ["paper","real"], "paper")`.
   When the answer is `real`, additionally require a `yesNo` confirmation
   defaulting to **false**: `"REAL money mode. Orders will use your live account. Continue?"`.
   A `false` answer returns exit code 1 without writing anything.
4. Step 2/7 — **KIS credentials.** `promptSecret("KIS app key")`,
   `promptSecret("KIS app secret")`, and
   `askValidated("KIS account number (10 digits)", validators.kisAccount10)`.
   Write all three with `keychainSet(KIS_SERVICE, kisAccount(mode, ...), value)`.
   Catch `KeychainLockedError`: print its message and return 1 — do not continue
   with a half-written keychain.
5. Step 3/7 — **Telegram.** `askValidated("Telegram bot token", validators.telegramToken)`
   captured via `promptSecret` (the token is a credential) and
   `askValidated("Telegram chat id", validators.telegramChatId)`.
   Store under `TELEGRAM_SERVICE`. Offer to skip the whole step with
   `yesNo("Configure Telegram notifications?", true)`; skipping is allowed and
   recorded only by the absence of the keychain items.
6. Step 4/7 — **LLM CLI.** Call `detectAgents()`; print one line per agent
   (found → path + version, missing → `not installed`). When none are found,
   `fail` and return 1. Otherwise `choose("Default agent", <found>, <first found>)`.
7. Step 5/7 — **Python + signal directory.** `findPython()`; when `null`, print
   the exact remediation line
   `"Install Python 3.11–3.13 (e.g. brew install python@3.12) and re-run."`
   and return 1. Then
   `askDefault("Signal directory (stock-signal-bot output)", join(homedir(),"stock-signal-bot","data","signals"))`
   validated with `validators.absolutePath`. When the directory does not exist,
   `warn` that no signals will be found until it does — but **do not** block:
   this is the documented external dependency.
8. Step 6/7 — **Bootstrap.** Build the `Config`, run `parseConfig` on it and
   abort with the collected errors if it is invalid (never save an invalid
   config), `saveConfig`, then `bootstrapPython(cfg, { onStep })` printing each
   step. A failed step prints its detail and returns 1.
9. Step 7/7 — **launchd.** For each `JobKey`, `yesNo("Install <job> (<schedule summary>)?", true)`.
   Install the accepted ones via `installJob`; report `loaded` vs not, and for
   not-loaded print `"run later: launchctl bootstrap gui/$UID <path>"`.
   Record the answers into `cfg.jobs` and `saveConfig` again.
10. Close with a summary block: config path, log directory, and the two next
    commands — `kis-trader doctor` and `kis-trader logs`.
11. Create `cli/__tests__/setup.test.ts` driving `runInit` with scripted input
    and fully stubbed `deps`.

## Deliverables
- `cli/setup.ts`
- `cli/__tests__/setup.test.ts`

## Verify
- `npm test` passes with at least these cases:
  - normal: a full happy-path script returns 0; assert `saveConfig` was called
    with `mode === "paper"` and that `keychainSet` was called exactly 5 times
    (3 KIS + 2 Telegram).
  - normal: answering "no" at the Telegram step returns 0 and `keychainSet` was
    called exactly 3 times.
  - error: `detectAgents` returning all `null` makes `runInit` return **1** and
    `saveConfig` is never called.
  - error: `keychainSet` throwing `KeychainLockedError` makes `runInit` return
    **1**, and the captured output contains `"keychain is locked"`.
  - error: `findPython` returning `null` makes `runInit` return 1 and the output
    contains `"Python 3.11–3.13"`.
  - boundary: choosing `real` and then answering **no** to the confirmation
    returns 1 with `saveConfig` never called.
  - boundary: a `signalDir` that does not exist still completes with exit 0, and
    the output contains a warning — the external-dependency gap warns, never blocks.
  - boundary: a `bootstrapPython` result whose second step is `ok:false` returns
    1 and `installJob` is never called.
  - Assert in at least one test that **no captured output line contains the
    secret values** fed to the prompts.

## Out of scope
- `doctor` (task 11) and subcommand dispatch (task 12).
