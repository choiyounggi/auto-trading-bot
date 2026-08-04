# Task 02: ASCII banner module

## Objective
`printBanner()` writes a coloured "KIS TRADER" ASCII block, the tagline, and the
package version + repo URL to a caller-supplied stream. A pure function
`renderBanner(version)` returns the same text as a string so it is testable
without touching `process.stdout`.

## Wiki pages (read these first, only these)
- None. `[no-wiki]` — terminal presentation only, no design decision the wiki owns.

## Inputs
- `package.json` from task 01 (for the version lookup path).
- Decisions that bind you: D1 (ESM), D2 (zero runtime deps — no chalk).

## Steps
1. Create `cli/banner.ts` exporting:
   - `export function renderBanner(version: string): string`
   - `export function printBanner(version: string, out?: NodeJS.WritableStream): void`
     — defaults to `process.stdout`, writes `renderBanner(version)`.
   - `export function readPackageVersion(pkgDir: string): string` — reads
     `<pkgDir>/package.json`, returns `pkg.version` when it is a string,
     otherwise the literal `"?"`. Never throws: a missing/unparseable file
     returns `"?"`.
2. Art: a 6-row "ANSI Shadow"-style block spelling `KIS TRADER`, stored as a
   `const ART: string[]` of exactly 6 strings. Colour each row with a 6-entry
   gradient array of ANSI 256-colour codes running green → cyan
   (`\x1b[38;5;42m`, `48m`, `50m`, `50m`, `48m`, `42m`), each row terminated
   with `\x1b[0m`. Indent every art row by two spaces.
3. Footer lines, after a blank line:
   - `  \x1b[1mLLM-driven KIS auto-trading\x1b[0m \x1b[2m— 한국투자증권 · Telegram · launchd\x1b[0m`
   - `  \x1b[2mv<version>  ·  https://github.com/choiyounggi/auto-trading-bot\x1b[0m`
   - then a trailing blank line.
4. Create `cli/__tests__/banner.test.ts` using `node:test` + `node:assert/strict`.

## Deliverables
- `cli/banner.ts`
- `cli/__tests__/banner.test.ts`

## Verify
- `npm test` passes with at least these 4 cases:
  - normal: `renderBanner("1.2.3")` contains `v1.2.3` and all 6 ART rows.
  - normal: `printBanner("1.2.3", fakeStream)` writes a non-empty string to the
    injected stream and writes nothing to `process.stdout`.
  - error: `readPackageVersion("/nonexistent/path")` returns exactly `"?"`
    (assert the value, and assert it did not throw).
  - boundary: `readPackageVersion` on a directory whose `package.json` has no
    `version` key returns `"?"`; and `renderBanner("")` still returns all 6 ART
    rows (empty version must not drop the footer).

## Out of scope
- Deciding *when* the banner prints (task 12 owns the `start`-suppression rule).
