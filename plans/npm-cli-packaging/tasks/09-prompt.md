# Task 09: interactive prompt helpers with non-echoing secret input

## Objective
Reusable prompts for the onboarding flow: `ask`, `askDefault`, `yesNo`,
`choose`, `askValidated`, and `promptSecret` — which reads a secret without
echoing it, and refuses to pretend it hid anything when stdin is not a TTY.

## Wiki pages (read these first, only these)
- wiki/security/secrets/secrets-in-code.md — use for: rule 1 (a secret's
  exposure surface must be minimised) and the edge-case row on secrets reaching
  logs; the terminal echo and the shell history are the surfaces here.

## Inputs
- Decisions that bind you: D2 (zero deps — no `inquirer`, no `prompts`),
  D8 (raw-mode non-echoing secret prompt with an explicit non-TTY fallback).

## Steps
1. Create `cli/prompt.ts`, built on `node:readline/promises` plus
   `node:process` `stdin`/`stdout`.
2. `export interface Io { input: NodeJS.ReadableStream & { isTTY?: boolean; setRawMode?: (b: boolean) => void }; output: NodeJS.WritableStream }`
   Every function takes an optional `io: Io` defaulting to
   `{ input: process.stdin, output: process.stdout }` so tests inject streams.
3. `export async function ask(q: string, io?: Io): Promise<string>` — trimmed answer.
4. `export async function askDefault(q: string, fallback: string, io?: Io): Promise<string>`
   — prompt renders as `` `${q} [${fallback}]: ` ``; empty answer returns `fallback`.
5. `export async function yesNo(q: string, defaultYes: boolean, io?: Io): Promise<boolean>`
   — hint `[Y/n]` or `[y/N]`; empty answer returns the default; otherwise true
   iff the answer lowercased starts with `y`.
6. `export async function choose<T extends string>(q: string, options: readonly T[], fallback: T, io?: Io): Promise<T>`
   — renders `` `${q} [${fallback}] (${options.join("/")}): ` ``; empty returns
   `fallback`; an unlisted answer re-prompts, up to 3 attempts, then returns
   `fallback`. Throws `new Error("choose() needs at least one option")` on an
   empty `options` array.
7. `export async function askValidated(q: string, validate: (s: string) => string | null, io?: Io, attempts = 3): Promise<string>`
   — `validate` returns an error message or `null` when the value is good.
   Writes the message and re-prompts. After `attempts` failures throws
   `new Error("could not read a valid value for: " + q)`.
8. `export async function promptSecret(q: string, io?: Io): Promise<string>`
   - When `io.input.isTTY` is true and `setRawMode` exists: set raw mode, read
     bytes until `\r` or `\n`, echo nothing (handle backspace `0x7f` by
     dropping the last char; `Ctrl-C` `0x03` rejects with
     `new Error("aborted")`), restore the previous mode in a `finally`, then
     write a newline so the next prompt starts on its own line.
   - Otherwise: write the line
     `"! stdin is not a TTY — input will be visible on screen"` to `io.output`
     first, then fall back to `ask`. Never silently echo a secret while
     implying it was hidden.
9. `export const validators` with:
   - `telegramToken(s)` → `null` when `/^\d+:[A-Za-z0-9_-]{20,}$/` matches, else
     `"That does not look like a BotFather token. Format: 1234:ABC..."`.
   - `telegramChatId(s)` → `null` when `/^-?\d{5,}$/` matches, else
     `"Chat id must be a number (negative for groups)."`.
   - `kisAccount10(s)` → `null` when `/^\d{10}$/` matches, else
     `"KIS account must be exactly 10 digits (8-digit CANO + 2-digit product code)."`.
   - `absolutePath(s)` → `null` when it starts with `/`, else
     `"Enter an absolute path starting with /."`.
   - `nonEmpty(s)` → `null` when `s.trim().length > 0`, else `"Value is required."`.
10. Create `cli/__tests__/prompt.test.ts`. Drive the functions with
    `stream.PassThrough` for input and a capturing writable for output.

## Deliverables
- `cli/prompt.ts`
- `cli/__tests__/prompt.test.ts`

## Verify
- `npm test` passes with at least these cases:
  - normal: `askDefault("Mode","paper", io)` with input `"\n"` resolves to `"paper"`.
  - normal: `yesNo("go?", true, io)` with input `"\n"` resolves `true`; with
    `"n\n"` resolves `false`.
  - normal: `validators.kisAccount10("1234567890")` returns `null`.
  - error: `askValidated` whose `validate` always fails rejects after 3 attempts
    with a message containing `"could not read a valid value"` — assert the
    error message, not just that it throws.
  - error: `validators.telegramToken("nope")` returns the exact BotFather
    message string.
  - error: `choose("a", [] as const, "x" as const, io)` rejects with
    `"choose() needs at least one option"`.
  - boundary: `promptSecret` on a **non-TTY** input resolves to the typed value
    **and** the captured output contains `"not a TTY"` — the warning is
    mandatory, not optional.
  - boundary: `validators.kisAccount10("123456789")` (9 digits) and
    `"12345678902"` (11 digits) both return the error message.
  - boundary: `validators.telegramChatId("-1001234567")` returns `null`
    (negative group ids are valid) while `"123"` returns the error.
  - boundary: `askDefault` with an answer of `"   "` (whitespace only) returns
    the fallback, not the spaces.

## Out of scope
- The onboarding flow that calls these (task 10).
