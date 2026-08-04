# Task 16: README, LICENSE, and package contents verification

## Objective
`README.md` documents install → `init` → `doctor` → run, including the
stock-signal-bot prerequisite and the paper/real distinction; `npm pack
--dry-run` ships exactly the intended files and nothing sensitive.

## Wiki pages (read these first, only these)
- wiki/security/dependencies/supply-chain.md — use for: rule 1 (commit the
  lockfile / reproducible installs) and the "You publish a library, not an app"
  edge case, which is what this package now is.
- wiki/security/secrets/secrets-in-code.md — use for: rule 5 (prevention), as
  the pack check is the last gate before anything could ship a credential.

## Inputs
- `package.json` `files` array from task 01 (D15)
- `cli/index.ts` `COMMANDS` from task 12 — the command list README must match
- Decisions that bind you: D15 (published file set), and the standing
  constraint: **do not run `npm publish`**.

## Steps
1. Rewrite `README.md` with these sections:
   - Title + one-paragraph description (LLM-driven KIS auto-trading, macOS).
   - **Prerequisites**: macOS, Node >= 20, Python 3.11–3.13, a KIS OpenAPI
     account (paper or real), optionally a Telegram bot, and at least one of
     claude/codex/pi/gemini on PATH.
   - **The signal dependency, stated plainly**: trading signals are produced by
     the separate `stock-signal-bot` project and read from
     `KIS_TRADER_SIGNAL_DIR`. Without it the engine runs and finds nothing to
     trade. Do not bury this.
   - **Install**: `npm i -g @younggichoi/kis-trader` and `npx @younggichoi/kis-trader init`.
   - **Commands**: a table generated to match `COMMANDS` exactly.
   - **Paper vs real**: default is `paper`; `real` places live orders and the
     engine's guardrails (`config/trading_rules.yaml`) are the only limiter.
   - **Where things live**: `~/.kis-trader/config.json` (0600), `~/.kis-trader/logs/`,
     Keychain services `kis-openapi` and `telegram-bot`, LaunchAgents
     `com.<user>.kistrader.*`.
   - **Risk notice**: this software places real orders; the author provides no
     warranty and users are responsible for their own trading losses.
   - **Troubleshooting**: `kis-trader doctor` first; the keychain-locked
     (`-25308`) case and its fix.
2. Add `LICENSE` — MIT, copyright holder `choiyounggi`, year 2026. It is
   referenced by `package.json` `files` and `license`, so its absence would
   break the pack.
3. Run `npm pack --dry-run` and inspect the file list.

## Deliverables
- `README.md` (rewritten)
- `LICENSE` (new)

## Verify
- `npm pack --dry-run 2>&1` output:
  - **contains** `dist/index.js`, `src/broker/kis_client.py`,
    `config/trading_rules.yaml`, `data/migrations/0001_init.sql`,
    `pyproject.toml`, `README.md`, `LICENSE`.
  - **does not contain** `tests/`, `plans/`, `dist-test/`, `node_modules/`,
    `data/logs/`, `data/signals/`, `.env`, `data/trades.sqlite`, or any
    `plists/` entry.
  - Assert each of these with an explicit grep and record the hit counts —
    "checked" without the counts is not evidence.
- `npm run build && npm test` → green.
- `.venv/bin/pytest -q` → green.
- `node dist/index.js help` exits 0.
- A README-vs-code consistency check: every `COMMANDS[i].name` appears in
  `README.md`. Run it as a one-liner and paste the result.
- `git status --porcelain` shows only intended files; no `dist/` or
  `node_modules/` is staged.

## Out of scope
- **`npm publish` — explicitly forbidden by the user.** Stop after the pack
  verification and report the tarball contents for approval.
