# Task 03: config schema, parse-and-validate, load/save

## Objective
`parseConfig(unknown)` turns untrusted JSON into a typed `Config` or a list of
per-key errors; `loadConfig()` reads `$KIS_TRADER_HOME/config.json` and
`saveConfig()` writes it at mode 0600. The exported `Config` type is *derived
from the parser*, not declared next to it.

## Wiki pages (read these first, only these)
- wiki/backend/node/boundaries/runtime-validation.md — use for: rules 1–3 and
  the "Env vars / config" row (validate the whole object once at startup, crash
  the boot on failure); and the Instead-of row that forbids a hand-written
  `interface` sitting beside a hand-written validator.
- wiki/infrastructure/config/environment-config.md — use for: rule 3 (validate
  the full schema at startup), rule 4 (the schema IS the key inventory), rule 5
  (required keys get no default).

## Inputs
- Decisions that bind you: D4 (derive the type from the parser; no zod),
  D5 (path, mode 0600, which keys are required vs defaulted), D12 (`signalDir`
  is required with no default).

## Steps
1. Create `cli/config.ts`.
2. Define the state dir resolver:
   `export function configHome(env = process.env): string` — returns
   `env.KIS_TRADER_HOME` when set and absolute, else `join(homedir(), ".kis-trader")`.
   A relative `KIS_TRADER_HOME` is rejected: throw `new Error("KIS_TRADER_HOME must be an absolute path")`.
3. Define the parser as the single source of truth:
   ```ts
   const REQUIRED = ["mode", "projectDir", "pythonPath", "signalDir"] as const;
   export type ParseResult =
     | { ok: true; value: Config }
     | { ok: false; errors: string[] };
   ```
   `Config` is `export type Config = { mode: "paper" | "real"; projectDir: string;
   pythonPath: string; signalDir: string; llmAgent: "claude" | "codex" | "pi" | "gemini";
   jobs: { orchestrator: boolean; monitor: boolean; reconciler: boolean; dipBuy: boolean } }`.
   Declare `Config` once here and have `parseConfig` return it — do not write a
   second interface elsewhere.
4. `export function parseConfig(input: unknown): ParseResult`:
   - non-object / null / array input → `{ ok:false, errors:["config must be a JSON object"] }`
   - each missing or non-string required key → error `"<key> is required"`;
     an empty-string value counts as missing.
   - `mode` not in `{"paper","real"}` → `"mode must be \"paper\" or \"real\""`
   - `projectDir` / `pythonPath` / `signalDir` not starting with `/` →
     `"<key> must be an absolute path"`
   - `llmAgent` absent → default `"claude"`; present but not in the four allowed
     values → `"llmAgent must be one of claude, codex, pi, gemini"`
   - `jobs` absent → default all four `true`; present but not an object → error
     `"jobs must be an object"`; each present job key that is not a boolean →
     `"jobs.<name> must be a boolean"`; absent job keys default to `true`.
   - Collect **all** errors, never stop at the first.
5. `export function loadConfig(home = configHome()): ParseResult` — reads
   `join(home, "config.json")`. Missing file → `{ ok:false, errors:["no config at <path> — run `kis-trader init` first"] }`.
   Unparseable JSON → `{ ok:false, errors:["<path> is not valid JSON"] }`.
6. `export function saveConfig(cfg: Config, home = configHome()): string` —
   `mkdirSync(home, { recursive: true })`, write pretty JSON, `chmodSync(path, 0o600)`,
   return the written path.
7. Create `cli/__tests__/config.test.ts`.

## Deliverables
- `cli/config.ts`
- `cli/__tests__/config.test.ts`

## Verify
- `npm test` passes with at least these cases:
  - normal: a fully-populated valid object parses; `result.value.mode === "paper"`.
  - normal: an object omitting `llmAgent` and `jobs` parses and yields
    `llmAgent === "claude"` and all four `jobs` values `true`.
  - error: an object missing `signalDir` returns `ok:false` and `errors`
    **contains exactly the string** `"signalDir is required"`.
  - error: `mode: "live"` returns `ok:false` with the exact message
    `'mode must be "paper" or "real"'`.
  - error: `loadConfig` against a temp dir with no `config.json` returns
    `ok:false` and an error mentioning `init`.
  - boundary: `parseConfig(null)`, `parseConfig([])` and `parseConfig("x")` each
    return `ok:false` with the object-shape message.
  - boundary: `projectDir: ""` produces `"projectDir is required"` (empty string
    is missing, not a valid absolute-path failure).
  - boundary: an object with **two** problems returns both errors, proving
    errors are accumulated.
  - `saveConfig` into a temp dir writes a file whose mode is `0o600`
    (`statSync(p).mode & 0o777`).

## Out of scope
- Prompting the user for these values (task 10) and checking that the paths
  actually exist on disk (task 11 `doctor`).
