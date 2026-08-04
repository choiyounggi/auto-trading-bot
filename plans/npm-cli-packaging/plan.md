# npm-cli-packaging

Goal: Turn this repo (a Python KIS auto-trading engine wired to one author's
machine) into `@younggichoi/kis-trader` — an npm-installable macOS CLI that
onboards a fresh user end to end: interactive `init` (KIS keys + Telegram +
local LLM CLI detection + Python venv bootstrap + launchd install), `doctor`,
`help`, an ASCII banner, and the usual lifecycle subcommands.

Acceptance criteria:
- `npm run build && npm test` green; `npm pack --dry-run` lists only intended files.
- `node dist/index.js help` prints usage; `node dist/index.js doctor` runs on a
  machine with no config and reports every check as a miss without crashing.
- `grep -rn "choeyeong-gi" --include=* .` returns 0 hits outside `plans/`.
- Existing pytest suite still passes.
- NOT done here: `npm publish` (user approves separately).

Stack: TypeScript 5.x → `tsc` → `dist/` (ESM, NodeNext), Node >= 20, **zero
runtime dependencies**; tests on built-in `node:test` + `node:assert/strict`.
Python engine unchanged in place (3.11+, pytest). macOS only (launchd).

Verified environment facts (checked 2026-08-04, not assumed):
- Node v25.8.1, npm 11.12.1, `node:test` available.
- `python3` is 3.14.6; the 3.11 the engine needs lives at
  `/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11`. Version
  detection is mandatory — `python3` is the wrong interpreter here.
- `security add-generic-password` usage text: *"Use of the -p or -w options is
  insecure. Specify -w as the last option to be prompted."* Interactive
  (`security -i`) mode parses the same command from **stdin**, keeping the
  secret out of `argv`/`ps`. Both paths return `-25308 User interaction is not
  allowed` when the login keychain is locked / the session is non-interactive.

## Decisions

| # | Decision | Choice | Wiki basis |
|---|----------|--------|------------|
| D1 | Runtime + entry shape | Node ESM. `bin` → `dist/index.js` with `#!/usr/bin/env node`. No bash shim (cliclaw needs one only because Bun can't be a shebang target; npm writes the PATH shim itself). | platforms-environment-path-resolution |
| D2 | Runtime dependencies | **Zero.** Every need is a `node:` builtin. No zod, no vitest, no commander. | security-dependencies-supply-chain (rule 2: stdlib covers it → add nothing; small utility → write in-repo) |
| D3 | Test runner | `node:test` + `node:assert/strict`, run via `node --test dist-test/` after `tsc`. | security-dependencies-supply-chain (rule 2) |
| D4 | Config validation | Hand-written `parseConfig(unknown): Config` returning a discriminated result; the TS type is **derived from the parser's return**, never declared beside it. Validated once at CLI startup; invalid config exits non-zero with the offending key named. | backend-node-boundaries-runtime-validation (rule 2/3, env-var row); infrastructure-config-environment-config (rule 3) |
| D5 | Config location + required keys | `~/.kis-trader/config.json`, mode 0600. Required with **no default**: `mode`, `projectDir`, `pythonPath`, `signalDir`. Defaulted: `llmAgent` (`"claude"`), `jobs` (all true). `KIS_TRADER_HOME` overrides the directory. | infrastructure-config-environment-config (rules 2, 5) |
| D6 | Secret storage | Never in `config.json`. macOS Keychain, exactly the services the Python side already reads: `kis-openapi` / `{paper\|real}-appkey`,`-secret`,`-account`; `telegram-bot` / `stock-trader`,`stock-trader-chatid`. | security-secrets-secrets-in-code (rule 1) |
| D7 | Keychain write mechanism | `security -i` with the command fed on **stdin** (`execFile("security", ["-i"], {input})`), so the secret never enters `argv`. `-25308` is detected and reported as "keychain locked — unlock and re-run", not swallowed. | security-secrets-secrets-in-code (rule 1); platforms-processes-non-interactive-cli-invocation (edge case: keychain prompt resolving only in a GUI session) |
| D8 | Secret prompt echo | `promptSecret()` puts stdin in raw mode and echoes nothing; falls back to a visible prompt with an explicit warning when stdin is not a TTY. | security-secrets-secrets-in-code (rule 1) |
| D9 | launchd jobs | Rendered at runtime from a TS template; the 8 committed plists are deleted. Label `com.<username>.kistrader.<job>`. Absolute paths only; `EnvironmentVariables` carries PATH/HOME/KIS_TRADER_HOME/KIS_MODE/KIS_TRADER_SIGNAL_DIR; `StandardOutPath`/`StandardErrorPath` under the state dir; install verified by `launchctl list`, not by the bootstrap exit code. | platforms-processes-background-services (rules 1,2,4 + version-manager edge case); platforms-environment-path-resolution (cron/launchd row) |
| D10 | Python interpreter discovery | Probe candidates in order and accept the first reporting 3.11–3.13; never accept bare `python3` without a version check. Absolute path is stored in config and written into the plists. | platforms-toolchains-version-management; platforms-environment-path-resolution |
| D11 | LLM CLI detection | Port cliclaw `lib/resolve-cli-path.ts` (well-known paths → nvm scan → login-shell `command -v` with sentinel). Version probes run with stdin at `/dev/null` and a hard timeout. | platforms-environment-path-resolution; platforms-processes-non-interactive-cli-invocation (rules 1,3) |
| D12 | Signal directory (the external-repo dependency) | Becomes config + `KIS_TRADER_SIGNAL_DIR` env. Python reads the env var and falls back to the current `~/stock-signal-bot/data/signals` so the author's running install does not break. `init` prompts for it; `doctor` checks existence and newest-file age. Porting the signal pipeline itself is out of scope. | infrastructure-config-environment-config (rules 2,5) |
| D13 | kis_client rate-limit bug | **In scope.** (a) `_headers()` resolves the token *before* stamping the throttle so the token POST is itself spaced and counted; (b) `get_balance()` retries the rate-limit rejection — 3 attempts total, capped exponential backoff with full jitter, `send_critical` only after the budget is spent. | backend-common-reliability-timeouts-and-retries (rule 4, and the 429 row of the failure-type table) |
| D14 | Test case set | Every new test file: ≥1 normal, ≥1 error, ≥1 boundary case; error cases assert the error *type and message*, not bare "throws". | testing-quality-minimum-case-set (rules 1,3,4) |
| D15 | Published file set | `files: ["dist/", "src/", "config/", "schemas/", "data/migrations/", "pyproject.toml", "README.md", "LICENSE"]`. Excludes `tests/`, `scripts/` dev-only helpers, `plans/`. | security-dependencies-supply-chain (lockfile/reproducibility) |
| D16 | `install_macbook_home.sh` | Deleted — author-only SSH deploy hardcoding `/Users/choeyeong-gi/stock-trader`. `[no-wiki]` | — |
| D17 | pyproject metadata | `name = "kis-trader"`; drop the unused `mcp>=0.9` dependency (0 import sites); description corrected off "키움 MCP". `[no-wiki]` | — |
| D18 | Live KIS check in `doctor` | The user asked for a real connectivity check, so `doctor` gets one — but it runs as `python -m src.broker.probe` in the **Python** process, which already owns the credentials, and returns a fixed JSON contract (`ok`/`mode`/`base_url`/`cano_masked`/`reason`/`detail`). The CLI parses that and never touches a key. `rate_limited` and `network` map to `warn`, never `fail`, so a throttled probe cannot be misread as bad credentials. | backend-common-reliability-timeouts-and-retries (429 row); security-secrets-secrets-in-code (rule 1) |

Tension noted on D2 vs D4: backend-node-boundaries-runtime-validation prescribes
a zod-style schema library. security-dependencies-supply-chain's add-vs-write row
sends a few-dozen-line need in-repo. Resolved in favour of in-repo because the
config is 6 keys and a runtime dep on a *credential-handling* CLI is a permanent
trust grant. The validation page's actual hazard — a hand-written `interface`
drifting from a hand-written validator — is avoided by D4's derive-the-type rule.

## Task order

| Task | Depends on | Parallel-ok |
|------|-----------|-------------|
| 01-package-skeleton | — | |
| 02-banner | 01 | parallel-ok with 03,04,05 |
| 03-config | 01 | parallel-ok with 02,04,05 |
| 04-keychain | 01 | parallel-ok with 02,03,05 |
| 05-resolve-cli-path | 01 | parallel-ok with 02,03,04 |
| 06-python-discovery | 01, 03 | |
| 07-launchd | 03, 06 | |
| 08-bootstrap | 06 | |
| 09-prompt | 01 | parallel-ok with 06,07,08 |
| 10-init | 03,04,05,06,07,08,09 | |
| 11b-kis-probe | — | parallel-ok with 01–10 (Python side) |
| 11-doctor | 03,04,05,06,07,08,11b | |
| 12-cli-entry | 02,10,11 | |
| 13-python-signal-dir | — | parallel-ok with 01-12 |
| 14-python-rate-limit | — | parallel-ok with 01-12 |
| 15-cleanup-author-env | 07, 13 | |
| 16-readme-and-pack | 01, 12, 15 | |
