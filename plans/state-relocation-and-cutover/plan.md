# state-relocation-and-cutover

Two phases, done in order.

**Phase A (code).** Move every piece of runtime state out of the installed
package directory and under `KIS_TRADER_HOME`, so `npm i -g …@latest` — which
`kis-trader upgrade` runs — stops destroying the trade history and the venv.

**Phase B (operations).** Cut the live trading system over from `~/stock-trader`
to the package, as a rehearsed, reversible runbook.

Acceptance criteria (A): after `init`, nothing the user cares about lives inside
the package directory; a real 0.2.0→0.3.0 global upgrade preserves
`trades.sqlite` and the venv, and re-installs Python dependencies; `npm test`
and `pytest` green.
Acceptance criteria (B): exactly one job set is loaded at any moment; the old
plists survive as a one-command rollback; `trades.sqlite` history carries over
with matching row counts; `doctor` passes on the new install.

Stack: unchanged — TypeScript CLI (Node ≥ 20, zero runtime deps, `node:test`)
over Python 3.11–3.13 (pytest). macOS, launchd.

## Measured facts (verified 2026-08-05, not assumed)

**The defect is real and reproduced.** Installing 0.1.0, creating
`<pkg>/data/trades.sqlite` and `<pkg>/.venv/bin/python`, then installing 0.2.0:
both files were **gone**. npm replaces the package directory, so anything not in
the tarball is deleted.

**The fix is viable — measured, not reasoned.** With the venv at a path *outside*
the package and the package installed editable into it:
- after the package directory was replaced, `import src.broker.kis_client` still
  resolved, and the venv survived;
- the newly-shipped `src.signal.main` was found too — so an upgrade picks up new
  code automatically;
- but it failed on `No module named 'pandas'` — 0.2.0 declares dependencies
  0.1.0 did not. **An upgrade must re-run the pip install**, or the new code runs
  against the old dependency set.

**Live system (Phase B target).** 9 jobs loaded as `com.choeyeonggi.*`.
`telegramagent` is a KeepAlive/RunAtLoad **daemon** (PID 770) exposing an
interactive Telegram trading interface (`/balance`, `/positions`, `/buy`,
`/sell` behind inline-button confirmation). `caffeinate.weekday` is an unrelated
sleep-inhibitor and is **not** part of this migration.
**Zero open positions** — all 51 rows in `positions` are `CLOSED`, last exit
2026-08-03 — so no holding is left unmonitored during a cutover. No
`KILL_SWITCH` file. Shared and therefore inherited automatically:
`~/.kis-token-paper.json` and the Keychain services `kis-openapi`,
`telegram-bot`, `signal-bot`. The package is **not** installed globally and
`~/.kis-trader/` does not exist yet.

**Coupling that shapes the task split.** `projectDir` is referenced in
`bootstrap.ts` (8), `index.ts` (6), `doctor.ts` (6), `config.ts` (6),
`setup.ts` (4), `launchd.ts` (1).

## Decisions

| # | Decision | Choice | Wiki basis |
|---|----------|--------|------------|
| D1 | State root | A new required config key **`stateDir`**, defaulting to `configHome()` (`~/.kis-trader`). `projectDir` narrows to "where the code is" and is no longer written to. Everything the user can lose — venv, `trades.sqlite`, logs, signals — lives under `stateDir`. | infrastructure-config-environment-config (rules 2, 4) |
| D2 | venv location | `<stateDir>/.venv`, with the package installed editable: `pip install -e <projectDir>[dev]`. **Measured**: this survives a package replacement and picks up the new code. | security-dependencies-supply-chain (rule 1, reproducible installs) |
| D3 | `upgrade` must re-bootstrap | After swapping the code, `upgrade` re-runs the pip install and then re-installs the jobs. Without it the new code meets the old dependency set — measured as `No module named 'pandas'`. | infrastructure-deploy-rollout-and-rollback (rule 2: the rollback/upgrade path is rehearsed, not improvised) |
| D4 | SQLite / logs / signals | `<stateDir>/data/trades.sqlite`, `<stateDir>/logs/`, `<stateDir>/data/signals/`. `defaultSignalDir` takes `stateDir`, not `projectDir`. | infrastructure-config-environment-config (rule 2) |
| D5 | `telegramAgent` becomes the 8th job | The daemon ships in the tarball (`src/agent/telegram_agent.py`) but has no `JOBS` entry, so a "full migration" would silently drop an interactive trading interface. `JobSpec.schedule` gains a `{ keepAlive: true }` form rendering `RunAtLoad` + `KeepAlive` + `ThrottleInterval 30`, matching the plist it replaces. | platforms-processes-background-services (macOS LaunchAgent row; rule 3 supervisor restart) |
| D6 | 06/07-style coupling, again | `JOB_KEYS` types both `Config.jobs` and `JOBS`, so widening it to 8 breaks `launchd.ts` in the same compile. `config.ts` + `launchd.ts` + `bootstrap.ts` are therefore **one task** (A1) — the state-path change moves through the same `Record<>` and `venvPython` seams. Splitting them repeats the defect this plan's predecessor hit. `[no-wiki]` | — |
| D7 | Migration path for existing installs | **None.** 0.1.0/0.2.0 have been published for hours, the only user is the author, and `~/.kis-trader/` does not exist — nobody has run `init`. Adding a migrator would be untested code guarding a case that cannot occur. Stated here so the omission is a decision, not an oversight. `[no-wiki]` | — |
| D8 | Cutover shape | Old plists are **booted out, never deleted**, and copied to `<stateDir>/rollback/` first. Rollback is one rehearsed command that re-bootstraps them. The rollback is executed once *before* the real cutover to prove it works. | infrastructure-deploy-rollout-and-rollback (rule 2; the "improvise rollback during an incident" Instead-of row) |
| D9 | Cutover ordering | Strictly: (1) rehearse rollback, (2) `KILL_SWITCH` on, (3) bootout all 8 old jobs, (4) observe `launchctl list` shows none, (5) copy `trades.sqlite`, verify row counts, (6) install new jobs, (7) observe all 8 loaded, (8) `doctor`, (9) `KILL_SWITCH` off. Never step 6 before step 4 completes — overlapping job sets means the same signal produces two orders, and launchd will not stop it because the labels differ. | backend-common-jobs-scheduled-job-overlap (rule 1); infrastructure-deploy-rollout-and-rollback (rule 1, gate each step) |
| D10 | Cutover window | Market closed **and** before the next scheduled action. Concretely: after 15:30 KST and before 22:35 (the US signal), or any weekend. Zero open positions today makes the risk unusually low, but Phase A ships first — there is no reason to rush a live cutover to meet tonight's 22:35. | infrastructure-deploy-rollout-and-rollback (rule 2) |
| D11 | History carry-over | Copy `~/stock-trader/data/trades.sqlite` to `<stateDir>/data/`, then verify `SELECT COUNT(*)` and `MAX(id)` match the source. Safety does not depend on it (no open positions) but PnL reporting continuity does. | infrastructure-data-backup-and-restore (verify the restore, do not assume it) |
| D12 | Phase B is a runbook, not code | It ships as `docs/cutover-runbook.md` with copy-pasteable commands and an observed check after every step. No test can prove a one-time live migration; the guard is the gate at each step plus the rehearsed rollback. `[no-wiki]` | — |

## Task order

| Task | Depends on | Wave | Files it owns |
|------|-----------|------|---------------|
| A1-state-root-contract | — | 1 | `cli/config.ts`, `cli/launchd.ts`, `cli/bootstrap.ts` + their tests |
| A2-consumers | A1 | 2 | `cli/setup.ts`, `cli/doctor.ts`, `cli/index.ts` + their tests |
| A3-upgrade-safety | A1, A2 | 3 | `cli/index.ts` upgrade path + `cli/__tests__/upgrade.test.ts` |
| A4-readme-and-version | A1–A3 | 4 | `README.md`, `package.json` |
| B1-cutover-runbook | A1–A4 | 5 | `docs/cutover-runbook.md` |

Waves are sequential on purpose. The `projectDir` → `stateDir` change moves
through `Record<JobName,…>`, `venvPython`, and `Config` — the same type seams
that forced a task merge in `plans/signal-pipeline-port`. Parallelism here would
buy minutes and re-earn that lesson.

**Phase B is executed with the user, not by a session.** It touches a live
trading account; the runbook is the deliverable, running it is a separate,
gated decision.
