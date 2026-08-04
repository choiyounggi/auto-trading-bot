# Task 04: macOS Keychain read/write wrapper

## Objective
`keychainSet()` stores a secret without ever placing it in `argv`, and
`keychainGet()`/`keychainHas()` read it back. A locked keychain (`-25308`) is
reported as a distinct, actionable error instead of a generic failure.

## Wiki pages (read these first, only these)
- wiki/security/secrets/secrets-in-code.md — use for: rule 1 (secrets load from
  a secret store at runtime, never from source/config files) and the edge-case
  row about a client leaking a secret through its own logging.
- wiki/platforms/processes/non-interactive-cli-invocation.md — use for: rule 1
  (detach fd 0 at the call site), rule 3 (bound every call with a timeout), and
  the edge case "a keychain/credential prompt that only resolves in a GUI session".

## Inputs
- Decisions that bind you: D6 (exact service/account names), D7 (`security -i`
  over stdin; detect `-25308`).
- Verified fact: `security add-generic-password` usage text says
  *"Use of the -p or -w options is insecure"*; `security -i` reads the same
  command from stdin. Both return `-25308 User interaction is not allowed`
  when the login keychain is locked.

## Steps
1. Create `cli/keychain.ts`.
2. Export the service/account constants so no caller re-types them:
   ```ts
   export const KIS_SERVICE = "kis-openapi";
   export const TELEGRAM_SERVICE = "telegram-bot";
   export function kisAccount(mode: "paper" | "real", kind: "appkey" | "secret" | "account"): string;
   // → `${mode}-${kind}`   e.g. "paper-appkey"
   export const TELEGRAM_TOKEN_ACCOUNT = "stock-trader";
   export const TELEGRAM_CHATID_ACCOUNT = "stock-trader-chatid";
   ```
   These must match `src/util/keychain.py` exactly — that Python module is the
   consumer and is not being changed.
3. `export class KeychainLockedError extends Error` with
   `name = "KeychainLockedError"` and message
   `"macOS keychain is locked or this session cannot prompt (-25308). Unlock the login keychain in a Terminal window and re-run."`
4. `export function keychainSet(service: string, account: string, secret: string, run = defaultRun): void`
   - Build the argument line
     `add-generic-password -U -s <service> -a <account> -w <secret>` and feed it
     to `security -i` on **stdin** via the injected `run`.
   - `defaultRun` is
     `(args: string[], input: string) => execFileSync("security", args, { input, encoding: "utf8", timeout: 10_000, stdio: ["pipe","pipe","pipe"] })`.
   - Reject an `account` or `service` containing whitespace, a newline, or a
     quote with `new Error("invalid keychain service/account name")` — they are
     interpolated into the stdin command line.
   - Reject a `secret` containing a newline with
     `new Error("secret must not contain a newline")` (it would terminate the
     stdin command).
   - When the thrown error's combined stdout+stderr contains `-25308` or
     `User interaction is not allowed`, rethrow `new KeychainLockedError()`.
     Any other failure rethrows
     `new Error("keychain write failed for <service>/<account>: <stderr first line>")`.
   - **Never** include `secret` in any thrown message or log line.
5. `export function keychainGet(service: string, account: string, run = defaultGet): string | null`
   - `defaultGet` runs
     `execFileSync("security", ["find-generic-password","-s",service,"-a",account,"-w"], { encoding:"utf8", timeout:10_000, stdio:["ignore","pipe","pipe"] })`
     — reading takes no secret argument, so argv is safe here.
   - Returns the trimmed value, or `null` when the item is absent (non-zero exit).
   - A `-25308` failure rethrows `KeychainLockedError` (absent ≠ locked).
6. `export function keychainHas(service: string, account: string, run?): boolean`
   — `keychainGet(...) !== null`.
7. Create `cli/__tests__/keychain.test.ts`. Inject fake `run` functions; the
   tests must never touch the real keychain.

## Deliverables
- `cli/keychain.ts`
- `cli/__tests__/keychain.test.ts`

## Verify
- `npm test` passes with at least these cases:
  - normal: `keychainSet` with a stub `run` — assert the stub received `["-i"]`
    as args and that the **stdin string** contains
    `add-generic-password -U -s kis-openapi -a paper-appkey -w SEKRIT`.
  - normal: `keychainGet` with a stub returning `"  value\n"` returns exactly
    `"value"`.
  - normal: `kisAccount("real","secret")` returns `"real-secret"`.
  - error: a stub `run` throwing an error whose `stderr` contains
    `"User interaction is not allowed"` makes `keychainSet` throw
    `KeychainLockedError` — assert `err.name === "KeychainLockedError"`.
  - error: a stub throwing a generic failure makes `keychainSet` throw an Error
    whose message contains `kis-openapi/paper-appkey` and **does not contain**
    the secret value.
  - error: `keychainSet(svc, acct, "line1\nline2")` throws with the exact
    message `"secret must not contain a newline"`.
  - boundary: `keychainSet("kis-openapi","a b","x")` throws
    `"invalid keychain service/account name"`.
  - boundary: `keychainGet` whose stub throws a plain non-zero exit returns
    `null` (absent), not a throw.
  - boundary: `keychainSet` with an empty-string secret is allowed through to
    `run` (the caller, not this layer, decides emptiness policy) — assert the
    stub was called.

## Out of scope
- Prompting for the values (task 09/10) and checking which keychain items a
  finished install should have (task 11 `doctor`).
