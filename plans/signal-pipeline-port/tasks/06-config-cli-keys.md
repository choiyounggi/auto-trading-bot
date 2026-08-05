# Task 06: extend the CLI config — news backend, signal jobs, signal dir

## Objective
`Config` carries a `newsLlmBackend` key (`none`/`claude`/`codex`/`pi`, default
`none`), and the documented default signal directory becomes the package's own
`data/signals` rather than a path in a separate project.

## Wiki pages (read these first, only these)
- wiki/infrastructure/config/environment-config.md — use for: rule 2 (a named
  config value per behaviour, read through one path), rule 4 (the schema IS the
  key inventory), rule 5 (required keys get no default — `newsLlmBackend` is
  optional and therefore *does* get one).
- wiki/backend/node/boundaries/runtime-validation.md — use for: rules 2–3, the
  parser stays the single source of truth and the type is derived from it.

## Inputs
- `cli/config.ts` — exports `Config`, `ParseResult`, `parseConfig`, `loadConfig`,
  `saveConfig`, `configHome`, `MODES`, `AGENTS`, `JOB_KEYS`, `JobName`
- `cli/__tests__/config.test.ts` — the existing suite; extend it, do not rewrite it
- Decisions that bind you: D9 (`newsLlmBackend` is separate from `llmAgent` and
  defaults to `none`), D5 (signalDir default), D8 (two new launchd jobs need
  their keys here — this task is the single owner of `cli/config.ts`).

## Steps
1. In `cli/config.ts` add, beside the existing `AGENTS`:
   ```ts
   export const NEWS_BACKENDS = ["none", "claude", "codex", "pi"] as const;
   export type NewsBackend = (typeof NEWS_BACKENDS)[number];
   ```
   Note it is deliberately **not** `AGENTS` — `gemini` is absent because the
   signal bot's `NEWS_LLM_BACKEND` does not support it, and `none` is present
   because "do not call an LLM" is the default (D9).
2. Add `newsLlmBackend: NewsBackend` to the `Config` interface.
3. In `parseConfig`, mirror how `llmAgent` is handled: absent → default `"none"`;
   present but not in `NEWS_BACKENDS` → push the error
   `` `newsLlmBackend must be one of ${NEWS_BACKENDS.join(", ")}` ``.
   Do not make it required, and do not derive it from `llmAgent`.
4. Export a documented default for the signal directory so `init` and the docs
   agree on one value:
   ```ts
   /** Where the bundled signal producer writes and the trader reads. */
   export function defaultSignalDir(projectDir: string): string {
     return join(projectDir, "data", "signals");
   }
   ```
   `signalDir` itself stays **required with no default** in `parseConfig` — this
   helper only supplies the prompt's suggestion (task 08 uses it).
5. Widen `JOB_KEYS` to the seven jobs the product now installs:
   `["orchestrator","monitor","reconciler","dipBuy","usOrchestrator","signalKr","signalUs"]`
   and add `signalKr: true`, `signalUs: true` to `parseConfig`'s `jobs` default.
   Task 07 builds its `JOBS` table against this list and asserts the two match,
   so the names must be exactly `signalKr` and `signalUs`.
6. Extend `cli/__tests__/config.test.ts`, including updating the existing
   `JOB_KEYS.length === 5` assertion to 7 and the `deepEqual` on the jobs default.

## Deliverables
- `cli/config.ts` (modified)
- `cli/__tests__/config.test.ts` (modified)

## Verify
- `npm test` green, with the config file's case count increased and these
  assertions present:
  - normal: an object omitting `newsLlmBackend` parses and yields `"none"`.
  - normal: `newsLlmBackend: "codex"` parses through.
  - normal: `defaultSignalDir("/opt/kis")` === `"/opt/kis/data/signals"`.
  - error: `newsLlmBackend: "gemini"` returns `ok:false` with the exact message
    `"newsLlmBackend must be one of none, claude, codex, pi"` — asserted with
    `deepEqual` on the error array so an extra spurious error also fails.
  - boundary: `NEWS_BACKENDS.length === 4` and it contains `"none"` but not
    `"gemini"` — this is the guard that keeps it from drifting into `AGENTS`.
  - boundary: the `saveConfig` round-trip test carries `newsLlmBackend` through
    unchanged.
  - boundary: a config with `llmAgent: "gemini"` and no `newsLlmBackend` still
    yields `newsLlmBackend: "none"` — proving they are independent (D9).
  - boundary: `JOB_KEYS.length === 7` and it contains `"signalKr"` and
    `"signalUs"` — the guard task 07 relies on.
  - boundary: omitting `jobs` defaults all **seven** to `true`.
  - error: `jobs: { signalKr: "yes" }` reports exactly
    `"jobs.signalKr must be a boolean"`.
- No existing config test regresses — run the whole suite.

## Out of scope
- `cli/launchd.ts` — task 07 owns the job table, schedules and plist rendering.
  This task only widens the key set it indexes against.
- Prompting for the value (task 08), checking it (task 09).
