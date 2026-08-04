import { test } from "node:test";
import assert from "node:assert/strict";

import {
  KIS_SERVICE,
  TELEGRAM_SERVICE,
  TELEGRAM_TOKEN_ACCOUNT,
  TELEGRAM_CHATID_ACCOUNT,
  KeychainLockedError,
  kisAccount,
  keychainGet,
  keychainHas,
  keychainSet,
} from "../keychain.js";

const SECRET = "SEKRIT";
const LOCKED_MESSAGE =
  "macOS keychain is locked or this session cannot prompt (-25308). " +
  "Unlock the login keychain in a Terminal window and re-run.";

interface WriteCall {
  args: string[];
  input: string;
}

/** A `run` stub for keychainSet that records what it was handed. */
function recordingRun(): { calls: WriteCall[]; run: (args: string[], input: string) => string } {
  const calls: WriteCall[] = [];
  return {
    calls,
    run: (args, input) => {
      calls.push({ args, input });
      return "";
    },
  };
}

/** Rebuild what `execFileSync` throws on a non-zero exit. */
function execError(stderr: string, stdout = ""): Error {
  const err = new Error("Command failed: security") as Error & {
    stderr: string;
    stdout: string;
    status: number;
  };
  err.stderr = stderr;
  err.stdout = stdout;
  err.status = 1;
  return err;
}

const throwingRun = (err: Error) => () => {
  throw err;
};

// ── normal ────────────────────────────────────────────────────────────

test("keychainSet feeds the whole command to `security -i` over stdin", () => {
  const { calls, run } = recordingRun();
  keychainSet(KIS_SERVICE, kisAccount("paper", "appkey"), SECRET, run);

  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].args, ["-i"]);
  assert.ok(
    calls[0].input.includes(
      "add-generic-password -U -s kis-openapi -a paper-appkey -w SEKRIT",
    ),
    `stdin was: ${calls[0].input}`,
  );
});

test("keychainSet never places the secret in argv", () => {
  const { calls, run } = recordingRun();
  keychainSet(KIS_SERVICE, kisAccount("real", "secret"), SECRET, run);

  assert.equal(
    calls[0].args.some((a) => a.includes(SECRET)),
    false,
    `argv leaked the secret: ${JSON.stringify(calls[0].args)}`,
  );
});

test("keychainGet trims the value security prints", () => {
  assert.equal(keychainGet(KIS_SERVICE, "paper-appkey", () => "  value\n"), "value");
});

test("keychainGet reads with find-generic-password -w and no secret in argv", () => {
  const seen: string[][] = [];
  keychainGet(TELEGRAM_SERVICE, TELEGRAM_TOKEN_ACCOUNT, (args) => {
    seen.push(args);
    return "token\n";
  });
  assert.deepEqual(seen[0], [
    "find-generic-password",
    "-s",
    "telegram-bot",
    "-a",
    "stock-trader",
    "-w",
  ]);
});

test("kisAccount composes `<mode>-<kind>` for every documented combination", () => {
  assert.equal(kisAccount("real", "secret"), "real-secret");
  assert.equal(kisAccount("paper", "appkey"), "paper-appkey");
  assert.equal(kisAccount("paper", "account"), "paper-account");
  assert.equal(kisAccount("real", "appkey"), "real-appkey");
});

test("the service/account constants match the Python consumer", () => {
  assert.equal(KIS_SERVICE, "kis-openapi");
  assert.equal(TELEGRAM_SERVICE, "telegram-bot");
  assert.equal(TELEGRAM_TOKEN_ACCOUNT, "stock-trader");
  assert.equal(TELEGRAM_CHATID_ACCOUNT, "stock-trader-chatid");
});

test("keychainHas is true when a value comes back and false when it is absent", () => {
  assert.equal(keychainHas(KIS_SERVICE, "paper-appkey", () => "v\n"), true);
  assert.equal(
    keychainHas(
      KIS_SERVICE,
      "paper-appkey",
      throwingRun(execError("security: item could not be found")),
    ),
    false,
  );
});

// ── error ─────────────────────────────────────────────────────────────

test("a locked keychain on write surfaces as KeychainLockedError", () => {
  assert.throws(
    () =>
      keychainSet(
        KIS_SERVICE,
        "paper-appkey",
        SECRET,
        throwingRun(execError("security: User interaction is not allowed.")),
      ),
    (err: Error) => {
      assert.equal(err.name, "KeychainLockedError");
      assert.ok(err instanceof KeychainLockedError);
      assert.equal(err.message, LOCKED_MESSAGE);
      return true;
    },
  );
});

test("the numeric -25308 code alone is enough to report a locked keychain", () => {
  assert.throws(
    () =>
      keychainSet(
        KIS_SERVICE,
        "paper-appkey",
        SECRET,
        throwingRun(execError("SecKeychainAddGenericPassword: -25308")),
      ),
    (err: Error) => err.name === "KeychainLockedError",
  );
});

test("a locked keychain on read is distinct from an absent item", () => {
  assert.throws(
    () =>
      keychainGet(
        KIS_SERVICE,
        "paper-appkey",
        throwingRun(execError("security: User interaction is not allowed.")),
      ),
    (err: Error) => {
      assert.equal(err.name, "KeychainLockedError");
      assert.equal(err.message, LOCKED_MESSAGE);
      return true;
    },
  );
});

test("a generic write failure names the item and never echoes the secret", () => {
  assert.throws(
    () =>
      keychainSet(
        KIS_SERVICE,
        "paper-appkey",
        SECRET,
        throwingRun(execError("security: SecKeychainItemCreateFromContent: write perm denied")),
      ),
    (err: Error) => {
      assert.equal(err.name, "Error");
      assert.ok(
        err.message.includes("kis-openapi/paper-appkey"),
        `message was: ${err.message}`,
      );
      assert.equal(
        err.message.includes(SECRET),
        false,
        `message leaked the secret: ${err.message}`,
      );
      return true;
    },
  );
});

test("a secret echoed back by security is redacted out of the thrown message", () => {
  assert.throws(
    () =>
      keychainSet(
        KIS_SERVICE,
        "paper-appkey",
        SECRET,
        throwingRun(execError(`security: unknown argument ${SECRET}`)),
      ),
    (err: Error) => {
      assert.equal(
        err.message.includes(SECRET),
        false,
        `message leaked the secret: ${err.message}`,
      );
      return true;
    },
  );
});

test("a newline in the secret is rejected before any process runs", () => {
  const { calls, run } = recordingRun();
  assert.throws(
    () => keychainSet(KIS_SERVICE, "paper-appkey", "line1\nline2", run),
    (err: Error) => {
      assert.equal(err.message, "secret must not contain a newline");
      return true;
    },
  );
  assert.equal(calls.length, 0, "the secret must never reach `run`");
});

test("a space in the secret is rejected, naming the secret as the problem", () => {
  const { calls, run } = recordingRun();
  assert.throws(
    () => keychainSet(KIS_SERVICE, "paper-appkey", "has space", run),
    (err: Error) => {
      assert.equal(err.message, "secret must not contain whitespace or a quote");
      return true;
    },
  );
  assert.equal(calls.length, 0, "a truncatable secret must never reach `run`");
});

test("every whitespace or quote character in a secret is rejected", () => {
  const { calls, run } = recordingRun();
  for (const secret of [
    "has space",
    "has\ttab",
    "has\rcr",
    'has"quote',
    "has'apostrophe",
    " leading",
    "trailing ",
    "has\u00a0nbsp", // a paste artifact, never a credential character
  ]) {
    assert.throws(
      () => keychainSet(KIS_SERVICE, "paper-appkey", secret, run),
      (err: Error) => {
        assert.equal(err.message, "secret must not contain whitespace or a quote");
        return true;
      },
      `${JSON.stringify(secret)} should have been rejected`,
    );
  }
  assert.equal(calls.length, 0);
});

// ── boundary ──────────────────────────────────────────────────────────

test("a service or account carrying whitespace, a newline or a quote is rejected", () => {
  const { calls, run } = recordingRun();
  for (const [service, account] of [
    ["kis-openapi", "a b"],
    ["kis openapi", "paper-appkey"],
    ["kis-openapi", "paper\nappkey"],
    ["kis-openapi", 'paper"appkey'],
    ["kis-openapi", "paper'appkey"],
    ["kis-openapi", "paper\tappkey"],
  ]) {
    assert.throws(
      () => keychainSet(service, account, "x", run),
      (err: Error) => {
        assert.equal(err.message, "invalid keychain service/account name");
        return true;
      },
      `${service}/${account} should have been rejected`,
    );
  }
  assert.equal(calls.length, 0);
});

test("a plain non-zero exit on read means absent, not an error", () => {
  assert.equal(
    keychainGet(
      KIS_SERVICE,
      "paper-appkey",
      throwingRun(
        execError(
          "security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.",
        ),
      ),
    ),
    null,
  );
});

test("an empty stored value reads back as an empty string, not as absent", () => {
  assert.equal(keychainGet(KIS_SERVICE, "paper-appkey", () => "\n"), "");
});

test("an empty secret is passed through — emptiness policy belongs to the caller", () => {
  const { calls, run } = recordingRun();
  keychainSet(KIS_SERVICE, "paper-appkey", "", run);
  assert.equal(calls.length, 1);
  assert.ok(calls[0].input.includes("-a paper-appkey -w "), `stdin was: ${calls[0].input}`);
});

test("a realistic KIS-style credential passes the whitespace guard untouched", () => {
  // The real shapes this module stores: a long base64 app secret, a base64url
  // token, a 10-digit account number. None may be rejected by the guard.
  const realistic = [
    "PSxvJ0v8mQ0aVJmQd3Fh9k+Lm/2NcOe1RrTuXyZaBcDeFgHiJkLmNoPqRsTuVwXy=",
    "7351234567:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
    "abc-DEF_123.xyz~",
    "5012345601",
  ];
  for (const secret of realistic) {
    const { calls, run } = recordingRun();
    keychainSet(KIS_SERVICE, kisAccount("real", "secret"), secret, run);
    assert.equal(calls.length, 1, `${secret} should have been accepted`);
    assert.ok(calls[0].input.includes(`-w ${secret}`), `stdin was: ${calls[0].input}`);
  }
});

test("an error carrying the lock code only on stdout is still reported as locked", () => {
  assert.throws(
    () =>
      keychainSet(
        KIS_SERVICE,
        "paper-appkey",
        SECRET,
        throwingRun(execError("", "User interaction is not allowed")),
      ),
    (err: Error) => err.name === "KeychainLockedError",
  );
});
