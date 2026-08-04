# Task 03b: extend JOB_KEYS with the overseas job

## Objective
`config.ts` knows all **five** launchd jobs, so `Config.jobs` and task 07's
`JobKey` are the same set and downstream code can index one by the other.

## Wiki pages (read these first, only these)
- wiki/infrastructure/config/environment-config.md — use for: rule 4, the config
  schema IS the key inventory, so a job the product installs must appear in it.

## Inputs
- `cli/config.ts` (task 03, already merged) — `JOB_KEYS`, `JobName`, `Config.jobs`,
  `parseConfig`
- `cli/__tests__/config.test.ts` (task 03, already merged)
- Decisions that bind you: D5 (`jobs` defaults to all-true).

## Why this exists
Task 03 shipped `JOB_KEYS = ["orchestrator","monitor","reconciler","dipBuy"]`
(4 entries) while task 07 specifies five jobs, adding `usOrchestrator`
(`-m src.orchestrator --asset-class overseas_stock`, weekdays 22:45). Tasks 10
and 11 iterate `JobKey` and index `cfg.jobs[key]`, which does not compile across
a 4-vs-5 mismatch. Fixing it in one place before those tasks run keeps every
session on the same contract.
(Plan repair, found by the orchestrator while extracting base-output signatures.)

## Steps
1. In `cli/config.ts`, change `JOB_KEYS` to
   `["orchestrator", "monitor", "reconciler", "dipBuy", "usOrchestrator"] as const`.
   `JobName` derives from it, so no other type edit is needed.
2. In `parseConfig`, the `jobs` default object gains `usOrchestrator: true`.
   Everything else in the function is already driven by `JOB_KEYS` — confirm it
   is, and do not special-case the new key.
3. Update `cli/__tests__/config.test.ts`:
   - the "omitting llmAgent and jobs applies the documented defaults" test's
     `deepEqual` gains `usOrchestrator: true`
   - the "partially specified jobs default the unlisted keys to true" test
     asserts `usOrchestrator === true`
   - the `saveConfig` round-trip `Config` literal gains a `usOrchestrator` value
   - add one new test asserting `JOB_KEYS.length === 5` and that it contains
     `usOrchestrator`, so a future 4-vs-5 drift fails loudly here.

## Deliverables
- `cli/config.ts` (modified)
- `cli/__tests__/config.test.ts` (modified)

## Verify
- `npm test` green, with the config file's case count **increased by one** and
  these specific assertions present:
  - normal: `parseConfig(validRaw()).value.jobs.usOrchestrator === true`
  - boundary: `JOB_KEYS.length === 5` and `JOB_KEYS.includes("usOrchestrator")`
  - boundary: `parseConfig({...validRaw(), jobs:{usOrchestrator:false}})` yields
    `usOrchestrator === false` with the other four still `true`
  - error: `parseConfig({...validRaw(), jobs:{usOrchestrator:"yes"}})` reports
    exactly `"jobs.usOrchestrator must be a boolean"`
- No other test in the file regresses (run the whole suite, not just this file).

## Out of scope
- `cli/launchd.ts` — task 07 owns the job table, schedules, and plist rendering.
  This task only widens the config key set it will index against.
