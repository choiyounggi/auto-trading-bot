import { test } from "node:test";
import assert from "node:assert/strict";
import { PassThrough, Writable } from "node:stream";

import {
  ask,
  askDefault,
  askValidated,
  choose,
  promptSecret,
  validators,
  yesNo,
  type Io,
} from "../prompt.js";

/** Yield one macrotask turn so the code under test can register its next read. */
const tick = (): Promise<void> => new Promise((r) => setImmediate(r));

type TestInput = PassThrough & {
  isTTY?: boolean;
  isRaw?: boolean;
  setRawMode?: (b: boolean) => void;
};

interface Harness {
  io: Io;
  input: TestInput;
  /** Every `setRawMode` argument, in order — proves the mode was restored. */
  rawCalls: boolean[];
  out(): string;
}

function makeIo(opts: { tty?: boolean } = {}): Harness {
  const input: TestInput = new PassThrough();
  const chunks: string[] = [];
  const output = new Writable({
    write(chunk, _enc, cb) {
      chunks.push(String(chunk));
      cb();
    },
  });
  const rawCalls: boolean[] = [];
  if (opts.tty) {
    input.isTTY = true;
    input.isRaw = false;
    input.setRawMode = (b: boolean) => {
      rawCalls.push(b);
      input.isRaw = b;
    };
  }
  return { io: { input, output }, input, rawCalls, out: () => chunks.join("") };
}

/**
 * Feed answers one at a time. A single batched write would arrive as one chunk
 * and readline would emit — and discard — every line past the pending question,
 * so each line waits a turn for the next prompt to be registered.
 */
async function feed(input: PassThrough, lines: readonly string[]): Promise<void> {
  for (const line of lines) {
    await tick();
    input.write(line);
  }
}

/** Run a prompt against a scripted set of answers. */
async function driven<T>(
  h: Harness,
  run: Promise<T>,
  lines: readonly string[],
): Promise<T> {
  await feed(h.input, lines);
  return run;
}

// ── normal ────────────────────────────────────────────────────────────

test("ask writes the question and returns the trimmed answer", async () => {
  const h = makeIo();
  const answer = await driven(h, ask("Name: ", h.io), ["  bob  \n"]);
  assert.equal(answer, "bob");
  assert.equal(h.out(), "Name: ");
});

test("askDefault renders the fallback in the prompt and returns a real answer", async () => {
  const h = makeIo();
  const answer = await driven(h, askDefault("Mode", "paper", h.io), ["real\n"]);
  assert.equal(answer, "real");
  assert.equal(h.out(), "Mode [paper]: ");
});

test("askDefault on an empty answer returns the fallback", async () => {
  const h = makeIo();
  assert.equal(await driven(h, askDefault("Mode", "paper", h.io), ["\n"]), "paper");
});

test("yesNo with defaultYes shows [Y/n] and an empty answer keeps the default", async () => {
  const h = makeIo();
  assert.equal(await driven(h, yesNo("go?", true, h.io), ["\n"]), true);
  assert.match(h.out(), /\[Y\/n\]/);
});

test("yesNo answered n returns false even when the default is yes", async () => {
  const h = makeIo();
  assert.equal(await driven(h, yesNo("go?", true, h.io), ["n\n"]), false);
});

test("yesNo without defaultYes shows [y/N] and an empty answer returns false", async () => {
  const h = makeIo();
  assert.equal(await driven(h, yesNo("go?", false, h.io), ["\n"]), false);
  assert.match(h.out(), /\[y\/N\]/);
});

test("yesNo accepts any answer starting with y, case-insensitively", async () => {
  for (const reply of ["y\n", "Y\n", "yes\n", "YES\n"]) {
    const h = makeIo();
    assert.equal(await driven(h, yesNo("go?", false, h.io), [reply]), true, reply);
  }
});

test("choose returns a listed option and renders every choice", async () => {
  const h = makeIo();
  const picked = await driven(
    h,
    choose("Agent", ["claude", "codex"] as const, "claude", h.io),
    ["codex\n"],
  );
  assert.equal(picked, "codex");
  assert.equal(h.out(), "Agent [claude] (claude/codex): ");
});

test("choose on an empty answer returns the fallback without re-prompting", async () => {
  const h = makeIo();
  const picked = await driven(
    h,
    choose("Agent", ["claude", "codex"] as const, "claude", h.io),
    ["\n"],
  );
  assert.equal(picked, "claude");
  assert.equal(h.out().match(/Agent/g)?.length, 1);
});

test("askValidated returns the value the validator accepts", async () => {
  const h = makeIo();
  const value = await driven(
    h,
    askValidated("Account: ", validators.kisAccount10, h.io),
    ["1234567890\n"],
  );
  assert.equal(value, "1234567890");
  assert.equal(h.out(), "Account: ");
});

test("validators.kisAccount10 accepts exactly ten digits", () => {
  assert.equal(validators.kisAccount10("1234567890"), null);
});

test("promptSecret on a TTY reads without echoing and restores the raw mode", async () => {
  const h = makeIo({ tty: true });
  const run = promptSecret("Token: ", h.io);
  await tick();
  h.input.write("hunter2\r");
  assert.equal(await run, "hunter2");
  assert.equal(h.out().includes("hunter2"), false, "the secret must never be echoed");
  assert.equal(h.out(), "Token: \n");
  assert.deepEqual(h.rawCalls, [true, false], "raw mode must be set then restored");
});

// ── error ─────────────────────────────────────────────────────────────

test("askValidated rejects with the exact message after three failed attempts", async () => {
  const h = makeIo();
  const rejected = assert.rejects(
    askValidated("Token", () => "bad token", h.io),
    (err: Error) => {
      assert.ok(err instanceof Error);
      assert.equal(err.message, "could not read a valid value for: Token");
      assert.match(err.message, /could not read a valid value/);
      return true;
    },
  );
  await feed(h.input, ["a\n", "b\n", "c\n"]);
  await rejected;
  assert.equal(h.out().match(/bad token/g)?.length, 3, "each failure is reported");
});

test("askValidated honours a custom attempt count", async () => {
  const h = makeIo();
  const rejected = assert.rejects(
    askValidated("Token", () => "bad token", h.io, 1),
    (err: Error) => {
      assert.equal(err.message, "could not read a valid value for: Token");
      return true;
    },
  );
  await feed(h.input, ["a\n"]);
  await rejected;
  assert.equal(h.out().match(/bad token/g)?.length, 1);
});

test("validators.telegramToken rejects a non-token with the BotFather message", () => {
  assert.equal(
    validators.telegramToken("nope"),
    "That does not look like a BotFather token. Format: 1234:ABC...",
  );
});

test("choose with no options rejects instead of returning the fallback", async () => {
  const h = makeIo();
  await assert.rejects(
    choose("a", [] as const, "x" as const, h.io),
    (err: Error) => {
      assert.ok(err instanceof Error);
      assert.equal(err.message, "choose() needs at least one option");
      return true;
    },
  );
  assert.equal(h.out(), "", "nothing is prompted when there is nothing to choose");
});

test("promptSecret rejects on Ctrl-C and still restores the raw mode", async () => {
  const h = makeIo({ tty: true });
  const rejected = assert.rejects(promptSecret("Token: ", h.io), (err: Error) => {
    assert.ok(err instanceof Error);
    assert.equal(err.message, "aborted");
    return true;
  });
  await tick();
  h.input.write(Buffer.from([0x61, 0x03]));
  await rejected;
  assert.deepEqual(h.rawCalls, [true, false], "raw mode is restored on abort too");
  assert.equal(h.out().includes("a"), false, "no typed character is echoed");
});

test("ask rejects when the input closes before an answer arrives", async () => {
  const h = makeIo();
  const rejected = assert.rejects(ask("Name: ", h.io), (err: Error) => {
    assert.ok(err instanceof Error);
    assert.equal(err.message, "input closed before an answer was given");
    return true;
  });
  await tick();
  h.input.end();
  await rejected;
});

test("every validator returns its exact message for a rejected value", () => {
  assert.equal(
    validators.telegramChatId("abc"),
    "Chat id must be a number (negative for groups).",
  );
  assert.equal(
    validators.kisAccount10("abcdefghij"),
    "KIS account must be exactly 10 digits (8-digit CANO + 2-digit product code).",
  );
  assert.equal(validators.absolutePath("relative/path"), "Enter an absolute path starting with /.");
  assert.equal(validators.nonEmpty(""), "Value is required.");
});

// ── boundary ──────────────────────────────────────────────────────────

test("promptSecret on a non-TTY warns before falling back to a visible read", async () => {
  const h = makeIo();
  const secret = await driven(h, promptSecret("Token: ", h.io), ["s3cret\n"]);
  assert.equal(secret, "s3cret");
  assert.ok(h.out().includes("not a TTY"), h.out());
  assert.equal(
    h.out(),
    "! stdin is not a TTY — input will be visible on screen\nToken: ",
  );
});

test("promptSecret falls back when the stream claims TTY but cannot go raw", async () => {
  const h = makeIo();
  h.input.isTTY = true; // no setRawMode — echo cannot actually be suppressed
  const secret = await driven(h, promptSecret("Token: ", h.io), ["s3cret\n"]);
  assert.equal(secret, "s3cret");
  assert.ok(h.out().includes("not a TTY"), h.out());
});

test("promptSecret applies backspace and drops control bytes", async () => {
  const h = makeIo({ tty: true });
  const run = promptSecret("Token: ", h.io);
  await tick();
  h.input.write("ab");
  await tick();
  // "X" then DEL cancels it; NUL is a control byte and is dropped outright.
  h.input.write("X\x7f\x00c\r");
  assert.equal(await run, "abc");
  assert.deepEqual(h.rawCalls, [true, false]);
});

test("promptSecret backspace on an empty buffer is a no-op", async () => {
  const h = makeIo({ tty: true });
  const run = promptSecret("Token: ", h.io);
  await tick();
  h.input.write("\x7f\x7fz\r");
  assert.equal(await run, "z");
});

test("promptSecret resolves with what was typed when the input ends unterminated", async () => {
  const h = makeIo({ tty: true });
  const run = promptSecret("Token: ", h.io);
  await tick();
  h.input.end("abc");
  assert.equal(await run, "abc");
  assert.deepEqual(h.rawCalls, [true, false]);
});

test("promptSecret restores a raw mode that was already on", async () => {
  const h = makeIo({ tty: true });
  h.input.isRaw = true;
  const run = promptSecret("Token: ", h.io);
  await tick();
  h.input.write("k\r");
  assert.equal(await run, "k");
  assert.deepEqual(h.rawCalls, [true, true], "the previous mode is restored, not a hardcoded false");
});

test("askDefault treats a whitespace-only answer as empty", async () => {
  const h = makeIo();
  assert.equal(await driven(h, askDefault("Mode", "paper", h.io), ["   \n"]), "paper");
});

test("choose re-prompts an unlisted answer and accepts a later valid one", async () => {
  const h = makeIo();
  const picked = await driven(
    h,
    choose("Agent", ["claude", "codex"] as const, "claude", h.io),
    ["gpt\n", "codex\n"],
  );
  assert.equal(picked, "codex");
  assert.equal(h.out().match(/Agent/g)?.length, 2);
});

test("choose gives up after three unlisted answers and returns the fallback", async () => {
  const h = makeIo();
  const picked = await driven(
    h,
    choose("Agent", ["claude", "codex"] as const, "claude", h.io),
    ["gpt\n", "gpt\n", "gpt\n"],
  );
  assert.equal(picked, "claude");
  assert.equal(h.out().match(/Agent/g)?.length, 3, "exactly three attempts, no more");
});

test("choose accepts a single-option list", async () => {
  const h = makeIo();
  const picked = await driven(h, choose("Agent", ["claude"] as const, "claude", h.io), ["\n"]);
  assert.equal(picked, "claude");
});

test("kisAccount10 rejects nine and eleven digits", () => {
  const msg = "KIS account must be exactly 10 digits (8-digit CANO + 2-digit product code).";
  assert.equal(validators.kisAccount10("123456789"), msg);
  assert.equal(validators.kisAccount10("12345678901"), msg);
  assert.equal(validators.kisAccount10(""), msg);
});

test("telegramChatId accepts negative group ids and rejects short ones", () => {
  assert.equal(validators.telegramChatId("-1001234567"), null);
  assert.equal(validators.telegramChatId("12345"), null);
  assert.equal(
    validators.telegramChatId("123"),
    "Chat id must be a number (negative for groups).",
  );
  assert.equal(
    validators.telegramChatId("-123"),
    "Chat id must be a number (negative for groups).",
  );
});

test("telegramToken needs a digit prefix and at least twenty secret characters", () => {
  const msg = "That does not look like a BotFather token. Format: 1234:ABC...";
  assert.equal(validators.telegramToken("1234:" + "A".repeat(20)), null);
  assert.equal(validators.telegramToken("1234:" + "A".repeat(19)), msg);
  assert.equal(validators.telegramToken(":" + "A".repeat(20)), msg);
  assert.equal(validators.telegramToken(""), msg);
});

test("absolutePath and nonEmpty handle their empty and whitespace edges", () => {
  assert.equal(validators.absolutePath("/"), null);
  assert.equal(validators.absolutePath(""), "Enter an absolute path starting with /.");
  assert.equal(validators.nonEmpty("   "), "Value is required.");
  assert.equal(validators.nonEmpty(" x "), null);
});
