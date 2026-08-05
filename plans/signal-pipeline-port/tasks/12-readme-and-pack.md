# Task 12: document the unified pipeline and re-verify the package contents

## Objective
`README.md` describes one product that produces its own signals, and
`npm pack --dry-run` ships the vendored signal code without shipping its tests,
backups, or secrets.

## Wiki pages (read these first, only these)
- wiki/security/dependencies/supply-chain.md — use for: rule 1 (reproducible
  installs, lockfile) and the "you publish a library" edge case.
- wiki/security/secrets/secrets-in-code.md — use for: rule 5 (prevention) — the
  pack check is the last gate before anything could ship a credential.

## Inputs
- `README.md` — currently opens with a section titled
  "The signal dependency — 먼저 읽을 것" declaring `stock-signal-bot` an external
  prerequisite. **That statement is now false** and is the main thing to fix.
- `cli/index.ts` — `COMMANDS` (the command table must keep matching it)
- `cli/config.ts` — `JOB_KEYS` (7 jobs; the Jobs table must list all seven)
- `package.json` — `files` array; `prepack` already strips `__pycache__`
- Decisions that bind you: D13 (never ship `.env` or `*.bak*`), and the standing
  **`npm publish` prohibition**.

## Steps
1. Rewrite the signal section of `README.md`. It must now say the package
   produces its own signals, and document:
   - the two signal jobs and their schedules, and **why the gap matters**
     (signal 16:30 → trade 16:45; signal 22:35 → trade 22:45)
   - that KRX credentials are optional and improve data quality
   - that Brave and the news backend are optional and default to off/`none`
   - that `signalDir` now defaults to `<install>/data/signals`
2. Update the Jobs table to all seven jobs, and the Prerequisites table: remove
   the `stock-signal-bot` row, keep Python/Node/KIS/LLM/Telegram.
3. Add a "How the chain runs" section: signal → JSON → trader reads → orders →
   monitor → reconciler, with the times.
4. Verify the command table still matches `COMMANDS` exactly.
5. Run `npm pack --dry-run` and check the contents item by item.

## Deliverables
- `README.md` (modified)

## Verify
Record the **hit count** for each grep — "checked" is not evidence.
- Present in the pack listing (expect 1 each): `src/signal/main.py`,
  `src/signal/data/dump_signals.py`, `src/signal/analysis/signal_engine.py`,
  `config/thresholds.yaml`, `config/overseas_watchlist.yaml`,
  `dist/index.js`, `README.md`, `LICENSE`.
- Absent from the pack listing (expect 0 each): `tests/`, `plans/`, `dist-test/`,
  `node_modules/`, `.env`, `*.bak`, `__pycache__`, `.pyc`, `data/signals/`,
  `data/logs/`, `trades.sqlite`.
- `grep -rn "stock-signal-bot" README.md | wc -l` — expect 0 except where it is
  explicitly framed as the *former* arrangement; if you keep such a mention,
  quote the line in the report.
- A README-vs-code check: every `COMMANDS[i].name` appears in `README.md`, and
  every one of the 7 `JOB_KEYS` appears in the Jobs table. Run it as a one-liner
  and paste the output.
- `npm run build && npm test` green; `.venv/bin/pytest -q` green.
- `git status --porcelain` shows only `README.md`.

## Out of scope
- **`npm publish` — forbidden.** Stop at the dry run and report the listing for
  approval.
- Bumping `version` in `package.json`. The release decision is the user's.
