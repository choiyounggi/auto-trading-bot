# Task B1: write the live-cutover runbook

## Objective
`docs/cutover-runbook.md` is a step-by-step, copy-pasteable procedure for moving
the running trading system from `~/stock-trader` to the installed package, where
every step has an observed check and the rollback is one rehearsed command.

## Wiki pages (read these first, only these)
- wiki/infrastructure/deploy/rollout-and-rollback.md — use for: rule 2
  (rollback-ready *before* the change; the previous artifact retained and
  directly deployable; rollback rehearsed, not improvised) and the Instead-of row
  against improvising rollback during an incident.
- wiki/backend/common/jobs/scheduled-job-overlap.md — use for: rule 1, assume
  overlap — two job sets loaded at once means one signal, two orders.
- wiki/platforms/processes/background-services.md — use for: rule 4, verify by
  observing `launchctl list`, never by a command's exit code.

## Inputs — measured state, do not re-derive
- Old jobs, all labelled `com.choeyeonggi.*`:
  `stocksignal`, `usstocksignal`, `dipbuy`, `telegramagent`, `usstockorch`,
  `dailyreconciler`, `tradeorch`, `posmonitor` — **eight to migrate**.
  `caffeinate.weekday` is a sleep inhibitor unrelated to trading: **leave it
  alone** and say so in the runbook.
- New labels are `com.<username>.kistrader.<job>` — different strings, so
  launchd will **not** refuse a double-load. Nothing but ordering prevents it.
- `~/stock-trader/data/trades.sqlite`: 51 rows in `positions`, all `CLOSED`,
  last exit 2026-08-03. Zero open positions.
- Shared and inherited automatically: `~/.kis-token-paper.json`, Keychain
  `kis-openapi` / `telegram-bot` / `signal-bot`.
- `KILL_SWITCH` is a file whose presence halts trading; it does not exist now.
- Decisions that bind you: D8 (bootout + retain, never delete), D9 (the ordering),
  D10 (window), D11 (history verification), D12 (this is a document).

## Steps
Write the runbook with these sections, each command followed by the check that
proves it worked:
1. **Preconditions** — market closed, before the next scheduled action (after
   15:30 KST and before 22:35, or a weekend); `doctor` on the new install
   already passing except for the job checks; a note to re-confirm open
   positions are zero at cutover time rather than trusting this document's
   snapshot.
2. **Rehearse the rollback first.** Copy the eight old plists to
   `<stateDir>/rollback/`, boot one job out, re-bootstrap it from the copy, and
   observe it in `launchctl list`. Only proceed once that round trip is seen to
   work — this is the step that turns rollback from a plan into a fact.
3. **Halt trading** — create `KILL_SWITCH` in the old tree.
4. **Boot out all eight**, then **observe** `launchctl list | grep choeyeonggi`
   returns nothing but `caffeinate`. Do not continue on a non-empty result.
5. **Carry history over** — copy `trades.sqlite`, then verify `COUNT(*)` and
   `MAX(id)` match the source. Print both numbers.
6. **Install the new jobs** (`kis-trader install-jobs`), then **observe** all
   eight `com.<user>.kistrader.*` labels in `launchctl list`.
7. **`kis-trader doctor`** — every check `pass` or `warn`, none `fail`.
8. **Resume trading** — remove `KILL_SWITCH`.
9. **First-run watch** — what to look for at the next scheduled fire, with the
   log paths under `<stateDir>/logs/`.
10. **Rollback procedure** — one block, runnable as-is: bootout the new eight,
    re-bootstrap the eight copies from `<stateDir>/rollback/`, restore
    `KILL_SWITCH` handling, observe.
11. **Retiring the old tree** — explicitly *later*, not part of this cutover.
    `~/stock-trader` and `~/stock-signal-bot` stay on disk untouched until the
    new install has run a full trading day; deleting them is a separate decision.

Include, near the top, the one hazard that ordering exists to prevent: while both
job sets are loaded, one signal produces two orders, and the differing labels
mean nothing warns you.

## Deliverables
- `docs/cutover-runbook.md`

## Verify
This task produces a document; the checks are on the document, and **none of its
commands may be executed here** — running them would migrate a live trading
system outside its approved window.
- Every step that changes state is followed by an observation command whose
  expected output is written out. Count the state-changing steps and the
  observation commands; report both numbers and confirm they match.
- The rollback block is self-contained: re-reading only that block is enough to
  execute it. Verify by reading it in isolation.
- Every path referenced exists or is created by an earlier step in the document —
  list them and mark which.
- `grep -c "caffeinate" docs/cutover-runbook.md` ≥ 1 (the job that must be left
  alone is named).
- `npm pack --dry-run 2>&1 | grep -c "docs/"` is **0** — the runbook is
  repository documentation, not shipped payload.

## Out of scope
- **Executing the cutover.** It touches a live account and is gated on the
  user's explicit go-ahead at a chosen window.
- Deleting `~/stock-trader` or `~/stock-signal-bot`.
