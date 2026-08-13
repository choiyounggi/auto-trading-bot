# Task A3: make `upgrade` re-install dependencies, and prove it

## Objective
`kis-trader upgrade` swaps the code, **re-runs the Python install**, and
re-installs the jobs — so new code never meets the previous release's dependency
set.

## Wiki pages (read these first, only these)
- wiki/infrastructure/deploy/rollout-and-rollback.md — use for: rule 2, the
  upgrade path is rehearsed rather than improvised, and the Instead-of row about
  verifying a release by watching rather than by a gate.
- wiki/security/dependencies/supply-chain.md — use for: rule 1, installs are
  reproducible; the pip step is what makes a release actually complete.

## Inputs
- `cli/index.ts` — `cmdUpgrade` (spawns `npm install -g <pkg>@latest`, then
  reinstalls the LaunchAgents)
- `cli/bootstrap.ts` — `bootstrapPython(cfg, opts)` from A1, which now installs
  editable into `<stateDir>/.venv`
- Decisions that bind you: D2, D3.

## The measurement this task exists for
With the venv outside the package and the package installed editable, replacing
the package directory kept imports working **and** exposed the newly shipped
module — but the run failed with `No module named 'pandas'`, because the new
release declared dependencies the old one did not. Swapping code without
re-installing is a half-upgrade that fails at the first new import.

## Steps
1. In `cmdUpgrade`, after the global install succeeds and before re-installing
   the jobs, run `bootstrapPython(cfg, {onStep})` and print each step. Abort with
   a non-zero exit if any step fails — an upgrade that left the venv stale must
   not silently proceed to re-arm the jobs.
2. Order is: install code → re-bootstrap Python → reinstall jobs. Jobs last, so
   a failed dependency install leaves the *old* jobs running rather than new
   jobs against a broken venv.
3. Extract the sequence into an exported pure-ish function
   `export async function runUpgrade(deps: UpgradeDeps): Promise<number>` where
   `UpgradeDeps` injects the npm spawn, `bootstrapPython`, and the job
   install/uninstall — so it is testable without touching the machine.
4. Create `cli/__tests__/upgrade.test.ts`.

## Deliverables
- `cli/index.ts` (modified)
- `cli/__tests__/upgrade.test.ts` (new)

## Verify
`npm test` green, with at least:
- normal: a successful run calls the three steps **in order** — assert the
  recorded call sequence, not just that each happened.
- normal: the return code is 0 and the job reinstall received every enabled job.
- error: the npm install failing returns non-zero and **neither** re-bootstraps
  **nor** touches the jobs.
- error: `bootstrapPython` returning a failed step returns non-zero and the jobs
  are **not** reinstalled — assert the job installer was never called, because
  re-arming jobs against a stale venv is the failure this ordering prevents.
- boundary: a config with some jobs disabled reinstalls only the enabled ones.
- boundary: `runUpgrade` performs no real spawn when every dep is injected —
  assert no child process was created (the test must not install anything).

## Out of scope
- Changing what `bootstrapPython` does — A1 owns it.
- README — A4.
