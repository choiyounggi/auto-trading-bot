/**
 * `runInit` drives seven interactive steps and writes to the keychain, the
 * config file, a venv, and launchd. None of that may happen here: every
 * collaborator is injected as a stub, and the only "input" is a pair of
 * in-memory streams. A test that reached the real machine would install
 * launchd jobs on the developer running `npm test`.
 *
 * Answers are fed by watching the output: every helper in `prompt.ts` leaves
 * the cursor on the prompt line (no trailing newline) while every message line
 * ends with one, so a write without "\n" is exactly a pending question. That
 * self-paces the script instead of guessing how many event-loop turns each
 * step needs.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { PassThrough, Writable } from "node:stream";
import { join } from "node:path";

import { runInit, type InitDeps } from "../setup.js";
import { JOB_KEYS, type Config } from "../config.js";
import {
  KIS_SERVICE,
  KeychainLockedError,
  TELEGRAM_CHATID_ACCOUNT,
  TELEGRAM_SERVICE,
  TELEGRAM_TOKEN_ACCOUNT,
} from "../keychain.js";
import type { StepResult } from "../bootstrap.js";
import type { DetectedAgent, SupportedCli } from "../resolve-cli-path.js";
import type { Io } from "../prompt.js";

const HOME = "/Users/tester/.kis-trader";
const PROJECT = "/Users/tester/auto-trading-bot";

/** Distinctive so an accidental echo is unmistakable in the captured output. */
const KIS_KEY = "KISAPPKEYVALUE0001";
const KIS_SECRET = "KISAPPSECRETVALUE0002";
const KIS_ACCT = "1234567890";
const TG_TOKEN = "999888:TOKENAAAABBBBCCCCDDDDEE";
const TG_CHAT = "123456789";
const SIGNAL_DIR = "/Users/tester/stock-signal-bot/data/signals";

/** Every value fed to a prompt that must never reach the screen or a log. */
const FED_SECRETS = [KIS_KEY, KIS_SECRET, TG_TOKEN, KIS_ACCT, TG_CHAT];

// ── harness ───────────────────────────────────────────────────────────

interface Harness {
  io: Io;
  out(): string;
  /** Answers not consumed — a mismatch means the flow asked a different set. */
  unused(): number;
}

function makeIo(answers: readonly string[]): Harness {
  const input = new PassThrough();
  const chunks: string[] = [];
  let next = 0;
  const output = new Writable({
    write(chunk, _enc, cb) {
      const text = String(chunk);
      chunks.push(text);
      if (!text.endsWith("\n")) {
        // Answer on the next turn, once readline has attached its listener.
        setImmediate(() => {
          if (next < answers.length) input.write(answers[next++] + "\n");
          // Out of script: close stdin so the pending read rejects instead of
          // hanging the whole test run.
          else input.end();
        });
      }
      cb();
    },
  });
  return {
    io: { input, output },
    out: () => chunks.join(""),
    unused: () => answers.length - next,
  };
}

interface Recorded {
  keychain: Array<{ service: string; account: string; secret: string }>;
  /** Snapshots — `runInit` mutates `cfg.jobs` between the two saves. */
  saved: Config[];
  installed: string[];
  bootstrapped: number;
}

const ALL_AGENTS: Record<SupportedCli, DetectedAgent | null> = {
  claude: { path: "/usr/local/bin/claude", version: "1.0.0" },
  codex: { path: "/usr/local/bin/codex", version: null },
  pi: null,
  gemini: null,
};

const OK_STEPS: StepResult[] = [
  { step: "venv", ok: true, detail: "created" },
  { step: "install", ok: true, detail: "installed" },
  { step: "sqlite", ok: true, detail: "ready" },
  { step: "migrate", ok: true, detail: "4 applied" },
];

function stubDeps(over: Partial<InitDeps> = {}): {
  deps: Partial<InitDeps>;
  rec: Recorded;
} {
  const rec: Recorded = { keychain: [], saved: [], installed: [], bootstrapped: 0 };
  const base: InitDeps = {
    detectAgents: () => ALL_AGENTS,
    findPython: () => "/opt/homebrew/bin/python3.12",
    keychainSet: (service, account, secret) => {
      rec.keychain.push({ service, account, secret });
    },
    saveConfig: (cfg, home) => {
      rec.saved.push(structuredClone(cfg));
      return join(home, "config.json");
    },
    bootstrapPython: (_cfg, opts) => {
      rec.bootstrapped += 1;
      for (const step of OK_STEPS) opts.onStep?.(step);
      return OK_STEPS;
    },
    installJob: (job) => {
      rec.installed.push(job);
      const label = `com.tester.kistrader.${job}`;
      return {
        label,
        path: `/Users/tester/Library/LaunchAgents/${label}.plist`,
        loaded: true,
        message: `${label} is loaded`,
      };
    },
    dirExists: () => true,
  };
  return { deps: { ...base, ...over }, rec };
}

/** One "y" per launchd job, derived so adding a job cannot silently starve the
 *  script — these tests assert every scripted answer is consumed, so a
 *  hardcoded count turns a new job into an opaque
 *  "input closed before an answer was given". */
const YES_PER_JOB: readonly string[] = JOB_KEYS.map(() => "y");

/** Answers for a full paper-mode run that says yes to everything. */
const HAPPY: readonly string[] = [
  "paper",
  KIS_KEY,
  KIS_SECRET,
  KIS_ACCT,
  "y",
  TG_TOKEN,
  TG_CHAT,
  "claude",
  SIGNAL_DIR,
  ...YES_PER_JOB,
];

async function run(
  answers: readonly string[],
  deps: Partial<InitDeps>,
  projectDir = PROJECT,
): Promise<{ code: number; h: Harness }> {
  const h = makeIo(answers);
  const code = await runInit({ home: HOME, projectDir, io: h.io, deps });
  return { code, h };
}

// ── normal ────────────────────────────────────────────────────────────

test("the happy path saves a paper config and writes five keychain items", async () => {
  const { deps, rec } = stubDeps();
  const { code, h } = await run(HAPPY, deps);

  assert.equal(code, 0);
  assert.equal(h.unused(), 0, "every scripted answer should be consumed");
  assert.equal(rec.keychain.length, 5, "3 KIS + 2 Telegram");
  assert.ok(rec.saved.length >= 1);
  assert.equal(rec.saved[0].mode, "paper");
  assert.equal(rec.saved[0].projectDir, PROJECT);
  assert.equal(rec.saved[0].pythonPath, "/opt/homebrew/bin/python3.12");
  assert.equal(rec.saved[0].signalDir, SIGNAL_DIR);
  assert.equal(rec.saved[0].llmAgent, "claude");
  assert.equal(rec.bootstrapped, 1);
  assert.deepEqual(rec.installed, [...JOB_KEYS]);
});

test("the KIS items land under the mode-scoped accounts the engine reads", async () => {
  const { deps, rec } = stubDeps();
  const { code } = await run(HAPPY, deps);

  assert.equal(code, 0);
  assert.deepEqual(rec.keychain.slice(0, 3), [
    { service: KIS_SERVICE, account: "paper-appkey", secret: KIS_KEY },
    { service: KIS_SERVICE, account: "paper-secret", secret: KIS_SECRET },
    { service: KIS_SERVICE, account: "paper-account", secret: KIS_ACCT },
  ]);
  assert.deepEqual(rec.keychain.slice(3), [
    { service: TELEGRAM_SERVICE, account: TELEGRAM_TOKEN_ACCOUNT, secret: TG_TOKEN },
    { service: TELEGRAM_SERVICE, account: TELEGRAM_CHATID_ACCOUNT, secret: TG_CHAT },
  ]);
});

test("declining Telegram still succeeds and writes only the three KIS items", async () => {
  const { deps, rec } = stubDeps();
  const answers = [
    "paper",
    KIS_KEY,
    KIS_SECRET,
    KIS_ACCT,
    "n",
    "claude",
    SIGNAL_DIR,
    ...YES_PER_JOB,
  ];
  const { code, h } = await run(answers, deps);

  assert.equal(code, 0);
  assert.equal(h.unused(), 0);
  assert.equal(rec.keychain.length, 3);
  assert.ok(
    rec.keychain.every((item) => item.service === KIS_SERVICE),
    "no Telegram item may be written when the step is skipped",
  );
});

test("no captured output line contains a value fed to a prompt", async () => {
  const { deps } = stubDeps();
  const { code, h } = await run(HAPPY, deps);

  assert.equal(code, 0);
  const out = h.out();
  // Guard against a vacuous pass: the secret prompts really were rendered, so
  // "no secret in the output" is a statement about a populated transcript.
  assert.ok(out.includes("KIS app key"));
  assert.ok(out.includes("KIS app secret"));
  assert.ok(out.includes("Telegram bot token"));
  for (const line of out.split("\n")) {
    for (const secret of FED_SECRETS) {
      assert.equal(
        line.includes(secret),
        false,
        `output leaked a prompted value: ${line}`,
      );
    }
  }
});

test("all seven step headers are printed in order", async () => {
  const { deps } = stubDeps();
  const { code, h } = await run(HAPPY, deps);

  assert.equal(code, 0);
  const out = h.out();
  let cursor = -1;
  for (let n = 1; n <= 7; n += 1) {
    const at = out.indexOf(`── Step ${n}/7 — `);
    assert.notEqual(at, -1, `missing header for step ${n}`);
    assert.ok(at > cursor, `step ${n} header is out of order`);
    cursor = at;
  }
});

test("the summary names the config path and the next two commands", async () => {
  const { deps } = stubDeps();
  const { code, h } = await run(HAPPY, deps);

  assert.equal(code, 0);
  const out = h.out();
  assert.ok(out.includes(join(HOME, "config.json")));
  assert.ok(out.includes(join(HOME, "logs")));
  assert.ok(out.includes("kis-trader doctor"));
  assert.ok(out.includes("kis-trader logs"));
});

test("confirming real mode scopes the keychain accounts to real", async () => {
  const { deps, rec } = stubDeps();
  const answers = [
    "real",
    "y",
    KIS_KEY,
    KIS_SECRET,
    KIS_ACCT,
    "n",
    "claude",
    SIGNAL_DIR,
    ...YES_PER_JOB,
  ];
  const { code, h } = await run(answers, deps);

  assert.equal(code, 0);
  assert.equal(h.unused(), 0);
  assert.equal(rec.saved[0].mode, "real");
  assert.deepEqual(
    rec.keychain.map((k) => k.account),
    ["real-appkey", "real-secret", "real-account"],
  );
});

test("job answers are recorded into the config and only accepted jobs install", async () => {
  const { deps, rec } = stubDeps();
  // Accept every other job, derived from JOB_KEYS so the expectations below
  // cannot drift out of step with the job list the way a hardcoded run of
  // answers does.
  const accepted = JOB_KEYS.filter((_, i) => i % 2 === 0);
  const declined = JOB_KEYS.filter((_, i) => i % 2 === 1);
  const answers = [
    "paper",
    KIS_KEY,
    KIS_SECRET,
    KIS_ACCT,
    "n",
    "claude",
    SIGNAL_DIR,
    ...JOB_KEYS.map((_, i) => (i % 2 === 0 ? "y" : "n")),
  ];
  const { code, h } = await run(answers, deps);

  assert.equal(code, 0);
  assert.equal(h.unused(), 0);
  assert.ok(accepted.length > 0 && declined.length > 0, "the split must exercise both branches");
  assert.deepEqual(rec.installed, [...accepted]);
  assert.equal(rec.saved.length, 2, "config is saved before and after launchd");
  assert.deepEqual(
    rec.saved[1].jobs,
    Object.fromEntries(JOB_KEYS.map((k, i) => [k, i % 2 === 0])),
  );
});

test("a job that installs but does not load prints the manual bootstrap line", async () => {
  const { deps } = stubDeps({
    installJob: (job) => ({
      label: `com.tester.kistrader.${job}`,
      path: `/Users/tester/Library/LaunchAgents/com.tester.kistrader.${job}.plist`,
      loaded: false,
      message: `launchctl bootstrap failed for ${job}: Input/output error`,
    }),
  });
  const { code, h } = await run(HAPPY, deps);

  assert.equal(code, 0, "a job that will not load must not abort the flow");
  assert.ok(
    h
      .out()
      .includes(
        "run later: launchctl bootstrap gui/$UID /Users/tester/Library/LaunchAgents/com.tester.kistrader.orchestrator.plist",
      ),
  );
  assert.ok(h.out().includes("Input/output error"));
});

// ── error ─────────────────────────────────────────────────────────────

test("no LLM CLI at all aborts before anything is saved", async () => {
  const { deps, rec } = stubDeps({
    detectAgents: () => ({ claude: null, codex: null, pi: null, gemini: null }),
  });
  const { code, h } = await run(
    ["paper", KIS_KEY, KIS_SECRET, KIS_ACCT, "n"],
    deps,
  );

  assert.equal(code, 1);
  assert.equal(rec.saved.length, 0);
  assert.equal(rec.bootstrapped, 0);
  assert.equal(rec.installed.length, 0);
  assert.ok(h.out().includes("not installed"));
});

test("a locked keychain aborts with its own message and no further writes", async () => {
  const { deps, rec } = stubDeps({
    keychainSet: () => {
      throw new KeychainLockedError();
    },
  });
  const { code, h } = await run(["paper", KIS_KEY, KIS_SECRET, KIS_ACCT], deps);

  assert.equal(code, 1);
  assert.ok(
    h.out().includes("keychain is locked"),
    "the user must be told to unlock, not shown an opaque failure",
  );
  assert.equal(rec.keychain.length, 0);
  assert.equal(rec.saved.length, 0);
});

test("a non-lock keychain failure also stops before a half-written keychain", async () => {
  const { deps, rec } = stubDeps({
    keychainSet: (service, account, secret) => {
      if (account === "paper-secret") {
        throw new Error("secret must not contain whitespace or a quote");
      }
      rec.keychain.push({ service, account, secret });
    },
  });
  const { code, h } = await run(["paper", KIS_KEY, KIS_SECRET, KIS_ACCT], deps);

  assert.equal(code, 1);
  assert.ok(h.out().includes("secret must not contain whitespace or a quote"));
  assert.equal(rec.saved.length, 0);
});

test("no usable Python aborts with the exact remediation line", async () => {
  const { deps, rec } = stubDeps({ findPython: () => null });
  const { code, h } = await run(
    ["paper", KIS_KEY, KIS_SECRET, KIS_ACCT, "n", "claude"],
    deps,
  );

  assert.equal(code, 1);
  assert.ok(
    h
      .out()
      .includes(
        "Install Python 3.11–3.13 (e.g. brew install python@3.12) and re-run.",
      ),
  );
  assert.equal(rec.saved.length, 0);
});

test("an invalid config is reported and never saved", async () => {
  const { deps, rec } = stubDeps();
  const { code, h } = await run(
    ["paper", KIS_KEY, KIS_SECRET, KIS_ACCT, "n", "claude", SIGNAL_DIR],
    deps,
    "relative/project/dir",
  );

  assert.equal(code, 1);
  assert.equal(rec.saved.length, 0, "an invalid config must never be written");
  assert.equal(rec.bootstrapped, 0);
  assert.ok(h.out().includes("projectDir must be an absolute path"));
});

// ── boundary ──────────────────────────────────────────────────────────

test("declining the real-money confirmation writes nothing and exits 1", async () => {
  const { deps, rec } = stubDeps();
  const { code, h } = await run(["real", "n"], deps);

  assert.equal(code, 1);
  assert.equal(rec.keychain.length, 0);
  assert.equal(rec.saved.length, 0);
  assert.equal(rec.installed.length, 0);
  assert.ok(
    h.out().includes("REAL money mode"),
    "the confirmation must state what real mode means",
  );
});

test("a signal directory that does not exist warns but never blocks", async () => {
  const { deps, rec } = stubDeps({ dirExists: () => false });
  const { code, h } = await run(HAPPY, deps);

  assert.equal(code, 0, "the external signal repo is a documented dependency");
  assert.equal(rec.saved[0].signalDir, SIGNAL_DIR);
  const warned = h
    .out()
    .split("\n")
    .some((line) => line.includes(SIGNAL_DIR) && line.includes("does not exist"));
  assert.ok(warned, "the missing directory must be called out");
});

test("a failed bootstrap step stops before any launchd job is installed", async () => {
  const failing: StepResult[] = [
    { step: "venv", ok: true, detail: "created" },
    { step: "install", ok: false, detail: "exit 1: No matching distribution found" },
  ];
  const { deps, rec } = stubDeps({
    bootstrapPython: (_cfg, opts) => {
      rec.bootstrapped += 1;
      for (const step of failing) opts.onStep?.(step);
      return failing;
    },
  });
  const { code, h } = await run(
    ["paper", KIS_KEY, KIS_SECRET, KIS_ACCT, "n", "claude", SIGNAL_DIR],
    deps,
  );

  assert.equal(code, 1);
  assert.equal(rec.installed.length, 0);
  assert.equal(rec.saved.length, 1, "the config written before bootstrap stays");
  assert.ok(h.out().includes("No matching distribution found"));
});

test("an unreadable Telegram token degrades to skipped instead of failing", async () => {
  const { deps, rec } = stubDeps();
  const answers = [
    "paper",
    KIS_KEY,
    KIS_SECRET,
    KIS_ACCT,
    "y",
    "not-a-token",
    "still-not",
    "nope-either",
    "claude",
    SIGNAL_DIR,
    ...YES_PER_JOB,
  ];
  const { code, h } = await run(answers, deps);

  assert.equal(code, 0, "an optional step must not fail the whole setup");
  assert.equal(h.unused(), 0);
  assert.equal(rec.keychain.length, 3, "no half-written Telegram pair");
  assert.ok(h.out().includes("BotFather"), "the validator's hint is shown");
});

test("an out-of-range choice falls back instead of blocking the run", async () => {
  const { deps, rec } = stubDeps();
  const answers = [
    "margin",
    "futures",
    "options",
    KIS_KEY,
    KIS_SECRET,
    KIS_ACCT,
    "n",
    "claude",
    SIGNAL_DIR,
    ...YES_PER_JOB,
  ];
  const { code, h } = await run(answers, deps);

  assert.equal(code, 0);
  assert.equal(h.unused(), 0);
  assert.equal(rec.saved[0].mode, "paper", "the safe default wins, never real");
});
