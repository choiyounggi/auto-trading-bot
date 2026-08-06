/**
 * `runUpgrade` swaps the installed code, re-runs the Python bootstrap, and
 * re-installs the launchd jobs — three operations that must never happen on
 * the machine running `npm test`. Every collaborator is injected through
 * `UpgradeDeps`, and each stub records itself into one shared `calls` array,
 * so the tests assert the *sequence* of calls, not merely that each happened.
 *
 * The ordering is the point of the command: jobs are re-installed last, so a
 * failed dependency install leaves the old jobs running instead of arming new
 * jobs against a broken venv.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { runUpgrade, type UpgradeDeps } from "../index.js";
import { JOB_KEYS, type Config } from "../config.js";
import type { StepResult } from "../bootstrap.js";

/** Paths are distinctive and nonexistent — nothing here may touch the disk. */
function makeConfig(over: Partial<Config> = {}): Config {
  return {
    mode: "paper",
    projectDir: "/Users/tester/auto-trading-bot",
    stateDir: "/Users/tester/.kis-trader",
    pythonPath: "/opt/homebrew/bin/python3.12",
    signalDir: "/Users/tester/.kis-trader/data/signals",
    llmAgent: "claude",
    newsLlmBackend: "none",
    jobs: Object.fromEntries(JOB_KEYS.map((j) => [j, true])) as Config["jobs"],
    ...over,
  };
}

/** The step names are `bootstrapPython`'s real ones, in its real order. */
const OK_STEPS: StepResult[] = [
  { step: "venv", ok: true, detail: "already exists" },
  { step: "pip-upgrade", ok: true, detail: "upgraded" },
  { step: "deps", ok: true, detail: "installed" },
  { step: "migrations", ok: true, detail: "2 applied" },
];

/** A bootstrap that died installing dependencies: shorter, last entry failed. */
const FAILED_STEPS: StepResult[] = [
  { step: "venv", ok: true, detail: "already exists" },
  { step: "pip-upgrade", ok: true, detail: "upgraded" },
  { step: "deps", ok: false, detail: "exit 1: No matching distribution found" },
];

function stubDeps(
  cfg: Config,
  over: Partial<UpgradeDeps> = {},
): { deps: UpgradeDeps; calls: string[] } {
  const calls: string[] = [];
  const deps: UpgradeDeps = {
    installCode: () => {
      calls.push("installCode");
      return { code: 0 };
    },
    config: () => cfg,
    bootstrapPython: (_cfg, opts) => {
      calls.push("bootstrapPython");
      for (const step of OK_STEPS) opts.onStep?.(step);
      return OK_STEPS;
    },
    installJob: (job) => {
      calls.push(`installJob:${job}`);
      const label = `com.tester.kistrader.${job}`;
      return {
        label,
        path: `/Users/tester/Library/LaunchAgents/${label}.plist`,
        loaded: true,
        message: `${label} is loaded`,
      };
    },
    ...over,
  };
  return { deps, calls };
}

/** The expected tail of a successful run: one install per enabled job, in
 *  `JOB_KEYS` order — derived, so adding a job cannot silently pass here. */
function installCallsFor(cfg: Config): string[] {
  return JOB_KEYS.filter((j) => cfg.jobs[j]).map((j) => `installJob:${j}`);
}

// ── normal ────────────────────────────────────────────────────────────

test("a successful upgrade runs install → bootstrap → jobs, in that order", async () => {
  const cfg = makeConfig();
  const { deps, calls } = stubDeps(cfg);

  const rc = await runUpgrade(deps);

  assert.equal(rc, 0);
  // The whole recorded sequence, not per-step booleans: this is what pins
  // "jobs last" — the property the ordering exists to protect.
  assert.deepEqual(calls, ["installCode", "bootstrapPython", ...installCallsFor(cfg)]);
});

test("a successful upgrade returns 0 and reinstalls every enabled job", async () => {
  const cfg = makeConfig();
  const { deps, calls } = stubDeps(cfg);

  const rc = await runUpgrade(deps);

  assert.equal(rc, 0);
  assert.deepEqual(
    calls.filter((c) => c.startsWith("installJob:")),
    JOB_KEYS.map((j) => `installJob:${j}`),
  );
});

// ── error ─────────────────────────────────────────────────────────────

test("a failed npm install propagates its code and stops before bootstrap and jobs", async () => {
  const cfg = makeConfig();
  const { deps, calls } = stubDeps(cfg, {
    installCode: () => {
      calls.push("installCode");
      return { code: 7 };
    },
  });

  const rc = await runUpgrade(deps);

  assert.equal(rc, 7);
  // Neither re-bootstraps nor touches the jobs: nothing after the failure.
  assert.deepEqual(calls, ["installCode"]);
});

test("npm never running at all returns non-zero without touching anything else", async () => {
  const cfg = makeConfig();
  const { deps, calls } = stubDeps(cfg, {
    installCode: () => {
      calls.push("installCode");
      return { code: 1, error: "spawn npm ENOENT" };
    },
  });

  const rc = await runUpgrade(deps);

  assert.notEqual(rc, 0);
  assert.deepEqual(calls, ["installCode"]);
});

test("a failed bootstrap step aborts non-zero and never calls the job installer", async () => {
  const cfg = makeConfig();
  const { deps, calls } = stubDeps(cfg, {
    bootstrapPython: (_cfg, opts) => {
      calls.push("bootstrapPython");
      for (const step of FAILED_STEPS) opts.onStep?.(step);
      return FAILED_STEPS;
    },
  });

  const rc = await runUpgrade(deps);

  assert.notEqual(rc, 0);
  // The call count, not the return code, is the assertion: re-arming jobs
  // against a stale venv is exactly the failure this ordering prevents.
  assert.equal(calls.filter((c) => c.startsWith("installJob:")).length, 0);
  assert.deepEqual(calls, ["installCode", "bootstrapPython"]);
});

// ── boundary ──────────────────────────────────────────────────────────

test("disabled jobs are skipped: only the enabled ones are reinstalled", async () => {
  const cfg = makeConfig();
  cfg.jobs.monitor = false;
  cfg.jobs.telegramAgent = false;
  const { deps, calls } = stubDeps(cfg);

  const rc = await runUpgrade(deps);

  assert.equal(rc, 0);
  const installed = calls.filter((c) => c.startsWith("installJob:"));
  assert.deepEqual(installed, installCallsFor(cfg));
  assert.ok(!installed.includes("installJob:monitor"));
  assert.ok(!installed.includes("installJob:telegramAgent"));
});

test("with every dep injected, runUpgrade creates no child process", async () => {
  // `runUpgrade`'s only PATH-resolved command is `npm`, so an emptied PATH is
  // a tripwire: a real spawn could not succeed, and the injected-config paths
  // do not exist, so a real bootstrap could not either. A clean exit whose
  // every step is accounted for by a stub therefore proves the run never left
  // this process — which is what lets this test run on a developer's machine.
  const cfg = makeConfig();
  const { deps, calls } = stubDeps(cfg);
  const savedPath = process.env.PATH;
  process.env.PATH = "";
  try {
    const rc = await runUpgrade(deps);
    assert.equal(rc, 0);
    assert.deepEqual(calls, ["installCode", "bootstrapPython", ...installCallsFor(cfg)]);
  } finally {
    process.env.PATH = savedPath;
  }
});
