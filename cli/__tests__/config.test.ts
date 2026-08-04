import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, statSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir, homedir } from "node:os";
import { join } from "node:path";

import {
  configHome,
  parseConfig,
  loadConfig,
  saveConfig,
  type Config,
} from "../config.js";

function tmp(): string {
  return mkdtempSync(join(tmpdir(), "kis-cfg-"));
}

/** A minimal object that satisfies every required key. */
function validRaw(): Record<string, unknown> {
  return {
    mode: "paper",
    projectDir: "/opt/kis",
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
  assert.deepEqual(r.value.jobs, {
    orchestrator: true,
    monitor: true,
    reconciler: true,
    dipBuy: true,
  });
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

test("a non-boolean job value is rejected by name", () => {
  const errs = errorsOf({ ...validRaw(), jobs: { monitor: "yes" } });
  assert.ok(errs.includes("jobs.monitor must be a boolean"), errs.join("; "));
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
      pythonPath: "/usr/bin/python3.11",
      signalDir: "/opt/signals",
      llmAgent: "pi",
      jobs: { orchestrator: true, monitor: false, reconciler: true, dipBuy: false },
    };
    const p = saveConfig(cfg, dir);
    assert.equal(statSync(p).mode & 0o777, 0o600);
    assert.match(readFileSync(p, "utf8"), /"mode": "real"/);

    const back = loadConfig(dir);
    assert.equal(back.ok, true);
    if (!back.ok) return;
    assert.deepEqual(back.value, cfg);
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
});
