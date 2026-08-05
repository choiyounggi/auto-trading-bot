/**
 * macOS Keychain read/write wrapper.
 *
 * Writing is the whole reason this module exists. `security`'s own usage text
 * says "Use of the -p or -w options is insecure" — the password lands in the
 * process's argv, which every user on the box can read out of `ps`. So the
 * write path never puts the secret in argv at all: it hands the *entire*
 * command line to `security -i` on stdin, which reads the same syntax from
 * standard input.
 *
 * Reading takes no secret argument, so `find-generic-password -w` is safe as
 * argv.
 *
 * Both paths run non-interactively. A `security` call against a locked login
 * keychain does not fail generically — it returns `-25308 User interaction is
 * not allowed`, the credential prompt that can only be answered in a GUI
 * session. That case gets its own error type so callers can tell the user to
 * unlock rather than reporting an opaque failure.
 *
 * The service/account names below are the wire contract with
 * `src/util/keychain.py`, which reads these same items at runtime. They must
 * match it exactly.
 */

import { execFileSync } from "node:child_process";

export const KIS_SERVICE = "kis-openapi";
export const TELEGRAM_SERVICE = "telegram-bot";
export const TELEGRAM_TOKEN_ACCOUNT = "stock-trader";
export const TELEGRAM_CHATID_ACCOUNT = "stock-trader-chatid";

/**
 * The signal pipeline's own credentials, read by `load_signal_keys()` in
 * `src/util/keychain.py`. The Python side looks each item up by these exact
 * strings, so a rename on either side is a silent runtime "missing", never a
 * compile error — `cli/__tests__/setup.test.ts` pins both halves together.
 */
export const SIGNAL_SERVICE = "signal-bot";
export const KRX_ID_ACCOUNT = "krx-id";
export const KRX_PW_ACCOUNT = "krx-pw";
export const BRAVE_KEY_ACCOUNT = "brave-api-key";

/** `security` is bounded so a blocked read fails the command instead of hanging a job. */
const TIMEOUT_MS = 10_000;

export function kisAccount(
  mode: "paper" | "real",
  kind: "appkey" | "secret" | "account",
): string {
  return `${mode}-${kind}`;
}

export class KeychainLockedError extends Error {
  constructor() {
    super(
      "macOS keychain is locked or this session cannot prompt (-25308). " +
        "Unlock the login keychain in a Terminal window and re-run.",
    );
    this.name = "KeychainLockedError";
  }
}

/** Feeds the command line to `security -i` on stdin; fd 0 is a pipe, never a terminal. */
type RunWrite = (args: string[], input: string) => string;

/** Reading needs no input, so fd 0 is detached outright. */
type RunRead = (args: string[]) => string;

const defaultRun: RunWrite = (args, input) =>
  execFileSync("security", args, {
    input,
    encoding: "utf8",
    timeout: TIMEOUT_MS,
    stdio: ["pipe", "pipe", "pipe"],
  });

const defaultGet: RunRead = (args) =>
  execFileSync("security", args, {
    encoding: "utf8",
    timeout: TIMEOUT_MS,
    stdio: ["ignore", "pipe", "pipe"],
  });

function text(value: unknown): string {
  return value === undefined || value === null ? "" : String(value);
}

/** Everything `security` printed, which is where the failure code shows up. */
function combinedOutput(err: unknown): string {
  const e = err as { stdout?: unknown; stderr?: unknown } | null;
  return `${text(e?.stdout)}\n${text(e?.stderr)}`;
}

function isLocked(err: unknown): boolean {
  const out = combinedOutput(err);
  return out.includes("-25308") || out.includes("User interaction is not allowed");
}

function firstLine(err: unknown): string {
  const e = err as { stderr?: unknown } | null;
  const stderr = text(e?.stderr).trim();
  const raw = stderr !== "" ? stderr : err instanceof Error ? err.message : text(err);
  return raw.split("\n")[0] ?? "";
}

/**
 * The names are interpolated into the stdin command line, where whitespace
 * separates arguments and quotes group them — a name carrying either would
 * change the command `security` ends up running.
 */
function assertName(service: string, account: string): void {
  if (/[\s"']/.test(service) || /[\s"']/.test(account)) {
    throw new Error("invalid keychain service/account name");
  }
}

/**
 * Store `secret` under `service`/`account`, replacing any existing item (`-U`).
 *
 * The secret reaches `security` only over stdin and never appears in a thrown
 * message — including when `security` echoes it back in its own diagnostics.
 */
export function keychainSet(
  service: string,
  account: string,
  secret: string,
  run: RunWrite = defaultRun,
): void {
  assertName(service, account);
  // A newline would terminate the command line and turn the rest of the secret
  // into a second `security` command. Checked before the general whitespace
  // rule below so this specific cause reports its specific remedy.
  if (secret.includes("\n")) {
    throw new Error("secret must not contain a newline");
  }
  // Measured, not theoretical: `security -i` splits the command line on
  // whitespace, so `-w has space` parses as `-w has` plus a positional
  // argument that add-generic-password reads as the *keychain name*. On an
  // unlocked keychain that stores the truncated value `has` and exits 0 — a
  // silently corrupted credential that later surfaces only as an auth
  // failure. Every credential this module stores (KIS app key/secret,
  // 10-digit account, Telegram token/chat id) is base64(url) or digits, so
  // none can legitimately carry whitespace or a quote: reject loudly.
  if (/[\s"']/.test(secret)) {
    throw new Error("secret must not contain whitespace or a quote");
  }

  const command = `add-generic-password -U -s ${service} -a ${account} -w ${secret}\n`;
  try {
    run(["-i"], command);
  } catch (err) {
    if (isLocked(err)) throw new KeychainLockedError();
    const detail = secret === "" ? firstLine(err) : firstLine(err).replaceAll(secret, "***");
    throw new Error(`keychain write failed for ${service}/${account}: ${detail}`);
  }
}

/**
 * Read the item back, or `null` when it does not exist.
 *
 * Absent and locked are deliberately different outcomes: a locked keychain
 * reported as "not configured" would send the user off to re-enter
 * credentials that are already stored.
 */
export function keychainGet(
  service: string,
  account: string,
  run: RunRead = defaultGet,
): string | null {
  try {
    return run(["find-generic-password", "-s", service, "-a", account, "-w"]).trim();
  } catch (err) {
    if (isLocked(err)) throw new KeychainLockedError();
    return null;
  }
}

export function keychainHas(
  service: string,
  account: string,
  run: RunRead = defaultGet,
): boolean {
  return keychainGet(service, account, run) !== null;
}
