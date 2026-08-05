# Task A4: document the state layout and cut 0.3.0

## Objective
`README.md` tells the truth about where state lives and why, and `package.json`
is at `0.3.0`.

## Wiki pages (read these first, only these)
- wiki/infrastructure/config/environment-config.md — use for: rule 4, one
  inventory of config keys — the README's "Where things live" section is the
  user-facing half of that inventory.

## Inputs
- `README.md` — its "Where things live" section currently describes
  `~/.kis-trader/` as holding only `config.json` and `logs/`
- `cli/config.ts` (A1) — `stateDir` is now a required key
- `cli/index.ts` (A3) — `upgrade` now re-bootstraps
- Decisions that bind you: D1, D2, D3, D4, D5.

## Steps
1. Update "Where things live": `<stateDir>` (default `~/.kis-trader`) holds
   `config.json`, `.venv/`, `data/trades.sqlite`, `data/signals/`, `logs/`,
   `locks/`. The package directory holds **only code** and is replaced wholesale
   on upgrade.
2. Add a short "Why state lives outside the package" note stating the measured
   fact plainly: npm replaces the package directory on upgrade, so anything
   written inside it is deleted. Two sentences, not an essay.
3. Document that `upgrade` re-installs Python dependencies, and that this is
   required because a new release may declare dependencies the old one did not.
4. Add `telegramAgent` to the Jobs table — mark it "always on (KeepAlive)"
   rather than giving it a time, and describe what it does (`/balance`,
   `/positions`, `/buy`, `/sell` with inline-button confirmation).
5. Bump `package.json` `version` to `0.3.0`. Do **not** publish.
6. Verify the Jobs table lists all eight `JOB_KEYS` and the command table still
   matches `COMMANDS`.

## Deliverables
- `README.md`, `package.json`

## Verify
- A one-liner asserting every `JOB_KEYS` entry appears in `README.md`, and every
  `COMMANDS[i].name` too — run it and paste the output.
- `grep -c "telegramAgent" README.md` ≥ 1.
- `node -e "console.log(require('./package.json').version)"` prints `0.3.0`.
- `npm run build && npm test` green; `.venv/bin/pytest -q` green.
- `npm pack --dry-run` still excludes `tests/`, `plans/`, `docs/`, `.env`,
  `*.pyc` — paste the hit count for each.
- `git status --porcelain` shows only `README.md` and `package.json`.

## Out of scope
- **`npm publish` — forbidden.** Publishing 0.3.0 is a separate, approved step.
- The cutover runbook — task B1.
