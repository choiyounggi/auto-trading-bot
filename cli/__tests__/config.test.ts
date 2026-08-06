import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, statSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir, homedir } from "node:os";
import { join } from "node:path";

import {
  JOB_KEYS,
  NEWS_BACKENDS,
  configHome,
  defaultSignalDir,
  parseConfig,
  loadConfig,
  saveConfig,
  type Config,
} from "../config.js";

/** The eight jobs, all enabled — the shape `parseConfig` defaults to. */
function allJobs(): Record<string, boolean> {
  return {
    orchestrator: true,
    monitor: true,
    reconciler: true,
    dipBuy: true,
    usOrchestrator: true,
    signalKr: true,
    signalUs: true,
    telegramAgent: true,
  };
}

function tmp(): string {
  return mkdtempSync(join(tmpdir(), "kis-cfg-"));
}

/**
 * A minimal object that satisfies every required key.
 *
 * `projectDir` and `stateDir` are deliberately different roots: the whole point
 * of `stateDir` is that runtime state does not live inside the installed
 * package, so a fixture that collapsed them would pass even if the two were
 * confused in the parser.
 */
function validRaw(): Record<string, unknown> {
  return {
    mode: "paper",
    projectDir: "/opt/kis",
    stateDir: "/var/lib/kis",
    pythonPath: "/usr/bin/python3.11",
    signalDir: "/opt/signals",
  };
}

function errorsOf(input: unknown): string[] {
  const r = parseConfig(input);
  assert.equal(r.ok, false, "expected parse to fail");
  return r.ok ? [] : r.errors;
}

// ── normal ────────────────────────────────────────────────────────────

test("a fully populated object parses", () => {
  const r = parseConfig({
    ...validRaw(),
    llmAgent: "codex",
    jobs: { orchestrator: false, monitor: true, reconciler: true, dipBuy: true },
  });
  assert.equal(r.ok, true);
  if (!r.ok) return;
  assert.equal(r.value.mode, "paper");
  assert.equal(r.value.llmAgent, "codex");
  assert.equal(r.value.jobs.orchestrator, false);
});

test("omitting llmAgent and jobs applies the documented defaults", () => {
  const r = parseConfig(validRaw());
  assert.equal(r.ok, true);
  if (!r.ok) return;
  assert.equal(r.value.llmAgent, "claude");
  assert.deepEqual(r.value.jobs, allJobs());
});

test("omitting newsLlmBackend defaults to none — enrichment costs nothing by default", () => {
  const r = parseConfig(validRaw());
  assert.equal(r.ok, true);
  if (!r.ok) return;
  assert.equal(r.value.newsLlmBackend, "none");
});

test("an explicit newsLlmBackend parses through unchanged", () => {
  const r = parseConfig({ ...validRaw(), newsLlmBackend: "codex" });
  assert.equal(r.ok, true);
  if (!r.ok) return;
  assert.equal(r.value.newsLlmBackend, "codex");
});

test("defaultSignalDir points at data/signals under the state root, not the package", () => {
  assert.equal(defaultSignalDir("/s"), "/s/data/signals");
  // The argument is the *state* root now. A signal dir inside the package
  // directory is destroyed by the next `npm i -g` that replaces it.
  assert.equal(defaultSignalDir("/var/lib/kis"), "/var/lib/kis/data/signals");
});

test("stateDir parses through unchanged and stays distinct from projectDir", () => {
  const r = parseConfig(validRaw());
  assert.equal(r.ok, true);
  if (!r.ok) return;
  assert.equal(r.value.stateDir, "/var/lib/kis");
  assert.equal(r.value.projectDir, "/opt/kis");
  assert.notEqual(r.value.stateDir, r.value.projectDir);
});

test("configHome honours an absolute KIS_TRADER_HOME", () => {
  assert.equal(configHome({ KIS_TRADER_HOME: "/custom/home" }), "/custom/home");
});

test("configHome falls back to ~/.kis-trader", () => {
  assert.equal(configHome({}), join(homedir(), ".kis-trader"));
});

// ── error ─────────────────────────────────────────────────────────────

test("a missing signalDir reports the exact required-key message", () => {
  const raw = validRaw();
  delete raw.signalDir;
  assert.ok(errorsOf(raw).includes("signalDir is required"));
});

test("a missing stateDir reports the exact required-key message and nothing else", () => {
  const raw = validRaw();
  delete raw.stateDir;
  // deepEqual, not `includes`: a stateDir that quietly acquired a parser
  // default (configHome(), projectDir, …) would make this list empty, and a
  // second spurious error would mean the absolute-path pass also ran on it.
  assert.deepEqual(errorsOf(raw), ["stateDir is required"]);
});

test("a relative stateDir is rejected with the exact absolute-path message", () => {
  assert.deepEqual(errorsOf({ ...validRaw(), stateDir: "relative/path" }), [
    "stateDir must be an absolute path",
  ]);
});

test("an invalid mode reports the exact allowed-values message", () => {
  const errs = errorsOf({ ...validRaw(), mode: "live" });
  assert.ok(errs.includes('mode must be "paper" or "real"'), errs.join("; "));
});

test("an invalid llmAgent reports the exact allowed-values message", () => {
  const errs = errorsOf({ ...validRaw(), llmAgent: "gpt" });
  assert.ok(
    errs.includes("llmAgent must be one of claude, codex, pi, gemini"),
    errs.join("; "),
  );
});

test("an invalid newsLlmBackend reports exactly the allowed-values message", () => {
  // `gemini` is the trap: it is a valid `llmAgent` but the signal bot's
  // NEWS_LLM_BACKEND does not support it. deepEqual so a spurious second error
  // (e.g. the key also being treated as required) fails the test too.
  assert.deepEqual(errorsOf({ ...validRaw(), newsLlmBackend: "gemini" }), [
    "newsLlmBackend must be one of none, claude, codex, pi",
  ]);
});

test("a non-string newsLlmBackend is rejected with the same message", () => {
  assert.deepEqual(errorsOf({ ...validRaw(), newsLlmBackend: 3 }), [
    "newsLlmBackend must be one of none, claude, codex, pi",
  ]);
});

test("a non-boolean job value is rejected by name", () => {
  const errs = errorsOf({ ...validRaw(), jobs: { monitor: "yes" } });
  assert.ok(errs.includes("jobs.monitor must be a boolean"), errs.join("; "));
  // telegramAgent is the newest key, so it is the one most likely to be
  // declared in the type but missed by the JOB_KEYS-driven validation loop.
  assert.deepEqual(errorsOf({ ...validRaw(), jobs: { telegramAgent: "yes" } }), [
    "jobs.telegramAgent must be a boolean",
  ]);
  // The keys added last are the ones most likely to miss the JOB_KEYS-driven
  // loop, so they are asserted exactly: one error, named, and nothing else.
  assert.deepEqual(errorsOf({ ...validRaw(), jobs: { usOrchestrator: "yes" } }), [
    "jobs.usOrchestrator must be a boolean",
  ]);
  assert.deepEqual(errorsOf({ ...validRaw(), jobs: { signalKr: "yes" } }), [
    "jobs.signalKr must be a boolean",
  ]);
  assert.deepEqual(errorsOf({ ...validRaw(), jobs: { signalUs: 1 } }), [
    "jobs.signalUs must be a boolean",
  ]);
});

test("loadConfig on a directory with no config.json points the user at init", () => {
  const dir = tmp();
  try {
    const r = loadConfig(dir);
    assert.equal(r.ok, false);
    if (r.ok) return;
    assert.equal(r.errors.length, 1);
    assert.match(r.errors[0], /init/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("loadConfig on unparseable JSON says so and names the file", () => {
  const dir = tmp();
  try {
    writeFileSync(join(dir, "config.json"), "{ nope");
    const r = loadConfig(dir);
    assert.equal(r.ok, false);
    if (r.ok) return;
    assert.match(r.errors[0], /not valid JSON/);
    assert.match(r.errors[0], /config\.json/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("configHome rejects a relative KIS_TRADER_HOME with a typed message", () => {
  assert.throws(
    () => configHome({ KIS_TRADER_HOME: "relative/dir" }),
    (err: Error) => {
      assert.equal(err.message, "KIS_TRADER_HOME must be an absolute path");
      return true;
    },
  );
});

// ── boundary ──────────────────────────────────────────────────────────

test("non-object inputs are rejected with the object-shape message", () => {
  for (const bad of [null, [], "x", 3, true]) {
    const errs = errorsOf(bad);
    assert.ok(
      errs.includes("config must be a JSON object"),
      `input ${JSON.stringify(bad)} gave: ${errs.join("; ")}`,
    );
  }
});

test("an empty-string required value counts as missing, not as a bad path", () => {
  const errs = errorsOf({ ...validRaw(), projectDir: "" });
  assert.ok(errs.includes("projectDir is required"), errs.join("; "));
  assert.equal(
    errs.includes("projectDir must be an absolute path"),
    false,
    "empty string must produce exactly one error, not two",
  );

  // Same rule for the key added by this task — an empty stateDir is missing,
  // not malformed.
  assert.deepEqual(errorsOf({ ...validRaw(), stateDir: "" }), ["stateDir is required"]);
  assert.deepEqual(errorsOf({ ...validRaw(), stateDir: "   " }), ["stateDir is required"]);
});

test("a non-string stateDir is rejected as missing rather than crashing the parser", () => {
  for (const bad of [3, null, [], {}, true]) {
    assert.deepEqual(
      errorsOf({ ...validRaw(), stateDir: bad }),
      ["stateDir is required"],
      `stateDir: ${JSON.stringify(bad)}`,
    );
  }
});

test("a relative required path is rejected as non-absolute", () => {
  const errs = errorsOf({ ...validRaw(), pythonPath: "python3" });
  assert.ok(errs.includes("pythonPath must be an absolute path"), errs.join("; "));
});

test("all errors are accumulated, not short-circuited at the first", () => {
  const errs = errorsOf({ mode: "live", projectDir: "", pythonPath: "rel", signalDir: "/ok" });
  assert.ok(errs.length >= 3, `expected >=3 accumulated errors, got: ${errs.join("; ")}`);
  assert.ok(errs.includes('mode must be "paper" or "real"'));
  assert.ok(errs.includes("projectDir is required"));
  assert.ok(errs.includes("pythonPath must be an absolute path"));
});

test("saveConfig writes mode 0600 and round-trips through loadConfig", () => {
  const dir = tmp();
  try {
    const cfg: Config = {
      mode: "real",
      projectDir: "/opt/kis",
      stateDir: "/var/lib/kis",
      pythonPath: "/usr/bin/python3.11",
      signalDir: "/opt/signals",
      llmAgent: "pi",
      newsLlmBackend: "claude",
      jobs: {
        orchestrator: true,
        monitor: false,
        reconciler: true,
        dipBuy: false,
        usOrchestrator: false,
        signalKr: true,
        signalUs: false,
        telegramAgent: false,
      },
    };
    const p = saveConfig(cfg, dir);
    assert.equal(statSync(p).mode & 0o777, 0o600);
    assert.match(readFileSync(p, "utf8"), /"mode": "real"/);
    assert.match(readFileSync(p, "utf8"), /"stateDir": "\/var\/lib\/kis"/);

    const back = loadConfig(dir);
    assert.equal(back.ok, true);
    if (!back.ok) return;
    assert.deepEqual(back.value, cfg);
    // Called out separately: a key the writer forgot would round-trip as
    // "stateDir is required" rather than as a wrong value.
    assert.equal(back.value.stateDir, "/var/lib/kis");
    // Called out separately: a key that is optional in the parser is exactly the
    // kind that survives a round-trip as its default instead of its saved value.
    assert.equal(back.value.newsLlmBackend, "claude");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("saveConfig creates the state directory when it does not exist", () => {
  const dir = tmp();
  const nested = join(dir, "deep", "state");
  try {
    const cfg = parseConfig(validRaw());
    assert.equal(cfg.ok, true);
    if (!cfg.ok) return;
    const p = saveConfig(cfg.value, nested);
    assert.equal(statSync(p).isFile(), true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("a jobs object that is not an object is rejected", () => {
  assert.ok(errorsOf({ ...validRaw(), jobs: "all" }).includes("jobs must be an object"));
});

test("partially specified jobs default the unlisted keys to true", () => {
  const r = parseConfig({ ...validRaw(), jobs: { monitor: false } });
  assert.equal(r.ok, true);
  if (!r.ok) return;
  assert.equal(r.value.jobs.monitor, false);
  assert.equal(r.value.jobs.orchestrator, true);
  assert.equal(r.value.jobs.reconciler, true);
  assert.equal(r.value.jobs.dipBuy, true);
  assert.equal(r.value.jobs.usOrchestrator, true);
  assert.equal(r.value.jobs.signalKr, true);
  assert.equal(r.value.jobs.signalUs, true);
  assert.equal(r.value.jobs.telegramAgent, true);

  // Same rule from the other side: opting one key out must not disturb the
  // others that were already there.
  const off = parseConfig({ ...validRaw(), jobs: { usOrchestrator: false } });
  assert.equal(off.ok, true);
  if (!off.ok) return;
  assert.deepEqual(off.value.jobs, { ...allJobs(), usOrchestrator: false });

  const noSignals = parseConfig({
    ...validRaw(),
    jobs: { signalKr: false, signalUs: false },
  });
  assert.equal(noSignals.ok, true);
  if (!noSignals.ok) return;
  assert.deepEqual(noSignals.value.jobs, {
    ...allJobs(),
    signalKr: false,
    signalUs: false,
  });
});

test("omitting jobs enables all eight, including both signal jobs and the daemon", () => {
  const r = parseConfig(validRaw());
  assert.equal(r.ok, true);
  if (!r.ok) return;
  assert.deepEqual(r.value.jobs, allJobs());
  assert.equal(Object.keys(r.value.jobs).length, 8);
});

test("JOB_KEYS is the whole eight-job inventory, in declared order", () => {
  assert.equal(JOB_KEYS.length, 8, `JOB_KEYS drifted: ${JOB_KEYS.join(", ")}`);
  // These names are a contract with the launchd job table — renaming any one
  // here silently unpairs a job from its schedule.
  assert.ok(JOB_KEYS.includes("signalKr"), JOB_KEYS.join(", "));
  assert.ok(JOB_KEYS.includes("signalUs"), JOB_KEYS.join(", "));
  assert.ok(JOB_KEYS.includes("telegramAgent"), JOB_KEYS.join(", "));
  assert.deepEqual([...JOB_KEYS], [
    "orchestrator",
    "monitor",
    "reconciler",
    "dipBuy",
    "usOrchestrator",
    "signalKr",
    "signalUs",
    "telegramAgent",
  ]);
  // Every key the parser defaults must come from JOB_KEYS and vice versa —
  // a key present in one but not the other is the 5-vs-7 defect this guards.
  const r = parseConfig(validRaw());
  assert.equal(r.ok, true);
  if (!r.ok) return;
  assert.deepEqual(Object.keys(r.value.jobs).sort(), [...JOB_KEYS].sort());
});

test("NEWS_BACKENDS is its own four-value list, not a copy of AGENTS", () => {
  assert.equal(NEWS_BACKENDS.length, 4, `NEWS_BACKENDS drifted: ${NEWS_BACKENDS.join(", ")}`);
  // `none` present: not calling an LLM is a supported choice, and the default.
  assert.ok(NEWS_BACKENDS.includes("none"), NEWS_BACKENDS.join(", "));
  // `gemini` absent: the signal bot's NEWS_LLM_BACKEND has no gemini path, so
  // letting it in would validate a value that fails at run time.
  assert.equal(
    (NEWS_BACKENDS as readonly string[]).includes("gemini"),
    false,
    NEWS_BACKENDS.join(", "),
  );
  assert.deepEqual([...NEWS_BACKENDS], ["none", "claude", "codex", "pi"]);
});

test("newsLlmBackend is independent of llmAgent", () => {
  // The trading agent being gemini must not select a news backend — they are
  // different decisions, and deriving one from the other would both pick an
  // unsupported value and start billing the trading agent for enrichment.
  const r = parseConfig({ ...validRaw(), llmAgent: "gemini" });
  assert.equal(r.ok, true);
  if (!r.ok) return;
  assert.equal(r.value.llmAgent, "gemini");
  assert.equal(r.value.newsLlmBackend, "none");

  // And the reverse: a news backend does not move llmAgent off its default.
  const back = parseConfig({ ...validRaw(), newsLlmBackend: "pi" });
  assert.equal(back.ok, true);
  if (!back.ok) return;
  assert.equal(back.value.llmAgent, "claude");
  assert.equal(back.value.newsLlmBackend, "pi");
});

test("signalDir stays required — defaultSignalDir is a suggestion, not a default", () => {
  const raw = validRaw();
  delete raw.signalDir;
  const r = parseConfig(raw);
  assert.equal(r.ok, false, "signalDir must not acquire a parser default");
  if (r.ok) return;
  assert.deepEqual(r.errors, ["signalDir is required"]);
});

test("defaultSignalDir joins onto any state dir without assuming a trailing slash", () => {
  assert.equal(defaultSignalDir("/var/lib/kis/"), "/var/lib/kis/data/signals");
  assert.equal(defaultSignalDir("/"), "/data/signals");
  assert.equal(defaultSignalDir(""), "data/signals");
});

test("stateDir stays required — configHome is init's suggestion, not a parser default", () => {
  // The mirror of the signalDir rule above, and the one that matters most:
  // silently defaulting stateDir to configHome() would point a hand-edited
  // config at a state root the user never chose, and the venv and trade
  // database would follow it there.
  const raw = validRaw();
  delete raw.stateDir;
  const r = parseConfig(raw);
  assert.equal(r.ok, false, "stateDir must not acquire a parser default");
  if (r.ok) return;
  assert.deepEqual(r.errors, ["stateDir is required"]);
  assert.equal(
    r.errors.some((e) => e.includes(configHome({ KIS_TRADER_HOME: "/x" }))),
    false,
    "the parser must not leak a suggested value into the error",
  );
});
