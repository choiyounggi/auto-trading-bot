import { test } from "node:test";
import assert from "node:assert/strict";

import {
  runDoctor,
  formatChecks,
  exitCodeFor,
  type Check,
  type DoctorDeps,
  type ProbeOutcome,
  type ProbeSpawn,
} from "../doctor.js";
import { JOB_KEYS, type Config } from "../config.js";
import { KIS_SERVICE, kisAccount, KeychainLockedError } from "../keychain.js";
import { venvPython } from "../bootstrap.js";

/**
 * Nothing here touches this machine. Every collaborator `runDoctor` has —
 * config load, keychain reads, agent detection, launchd status, filesystem
 * probes, the Python spawn and the clock — is injected, so these tests never
 * read the real keychain, never call `launchctl`, never create a venv and never
 * start a Python process. The paths below are strings that are only ever
 * formatted into messages.
 */

const PROJECT = "/Users/tester/project";
const SIGNAL_DIR = "/Users/tester/signals";
const PYTHON = "/opt/homebrew/bin/python3.12";

/** Fixed clock — signal freshness must never depend on wall time. */
const NOW = Date.UTC(2026, 0, 15, 12, 0, 0);
const HOUR = 3_600_000;

function cfg(over: Partial<Config> = {}): Config {
  return {
    mode: "paper",
    projectDir: PROJECT,
    pythonPath: PYTHON,
    signalDir: SIGNAL_DIR,
    llmAgent: "claude",
    jobs: {
      orchestrator: true,
      monitor: true,
      reconciler: true,
      dipBuy: true,
      usOrchestrator: true,
    },
    ...over,
  };
}

function probeJson(over: Record<string, unknown> = {}): string {
  return (
    JSON.stringify({
      ok: true,
      mode: "paper",
      base_url: "https://openapivts.koreainvestment.com:29443",
      cano_masked: "****0180",
      reason: "",
      detail: "잔고 조회 성공",
      ...over,
    }) + "\n"
  );
}

function okProbe(): ProbeOutcome {
  return { code: 0, stdout: probeJson(), stderr: "" };
}

/** All-healthy stubs; `over` replaces individual collaborators. */
function healthy(
  over: Partial<DoctorDeps> = {},
  config: Config = cfg(),
): Partial<DoctorDeps> {
  return {
    loadConfig: () => ({ ok: true, value: config }),
    pythonVersion: () => [3, 12],
    isExecutableFile: () => true,
    fileExists: () => true,
    signalEntries: () => [{ name: "kospi.json", mtimeMs: NOW - 2 * HOUR }],
    keychainHas: () => true,
    detectAgents: () => ({
      claude: { path: "/usr/local/bin/claude", version: "1.2.3" },
      codex: null,
      pi: null,
      gemini: null,
    }),
    jobStatus: () => "loaded",
    runProbe: () => okProbe(),
    now: () => NOW,
    ...over,
  };
}

function get(checks: Check[], name: string): Check {
  const hit = checks.find((c) => c.name === name);
  assert.ok(hit, `no check named ${name} in [${checks.map((c) => c.name).join(", ")}]`);
  return hit;
}

interface ProbeCall {
  command: string;
  args: string[];
  opts: ProbeSpawn;
}

/** Records every probe spawn so tests can assert the contract — and its absence. */
function recordingProbe(outcome: ProbeOutcome = okProbe()): {
  calls: ProbeCall[];
  run: DoctorDeps["runProbe"];
} {
  const calls: ProbeCall[] = [];
  return {
    calls,
    run: (command, args, opts) => {
      calls.push({ command, args, opts });
      return outcome;
    },
  };
}

// ---------------------------------------------------------------- normal path

test("all-healthy stubs: every check passes and the exit code is 0", () => {
  const checks = runDoctor(healthy());

  assert.deepEqual(
    checks.map((c) => c.name),
    [
      "config",
      "python",
      "venv",
      "database",
      "keychain-kis",
      "keychain-telegram",
      "llm-agent",
      "signal-dir",
      "kis-api",
      ...JOB_KEYS.map((k) => `job:${k}`),
    ],
  );
  for (const c of checks) {
    assert.equal(c.status, "pass", `${c.name} was ${c.status}: ${c.detail}`);
    assert.notEqual(c.detail, "", `${c.name} has an empty detail`);
  }
  assert.equal(exitCodeFor(checks), 0);
});

test("every non-pass check carries a remediation hint", () => {
  const checks = runDoctor(
    healthy({
      pythonVersion: () => null,
      isExecutableFile: () => false,
      fileExists: () => false,
      keychainHas: () => false,
      detectAgents: () => ({ claude: null, codex: null, pi: null, gemini: null }),
      signalEntries: () => null,
      jobStatus: () => "installed-not-loaded",
    }),
  );

  const nonPass = checks.filter((c) => c.status !== "pass");
  assert.ok(nonPass.length >= 8, `expected many non-pass checks, got ${nonPass.length}`);
  for (const c of nonPass) {
    assert.ok(
      c.hint !== undefined && c.hint.trim() !== "",
      `${c.name} (${c.status}) has no hint`,
    );
  }
});

test("formatChecks marks a pass with ✔ and puts a warn's hint on its own line", () => {
  const checks = runDoctor(healthy({ keychainHas: (service) => service === KIS_SERVICE }));
  const telegram = get(checks, "keychain-telegram");
  assert.equal(telegram.status, "warn");
  assert.ok(telegram.hint);

  const out = formatChecks(checks);
  const lines = out.split("\n");

  assert.ok(
    lines.some((l) => l.startsWith("✔") && l.includes("config")),
    `no ✔ line for the passing config check:\n${out}`,
  );
  const hintLine = lines.find((l) => l.trim() === `↳ ${telegram.hint}`);
  assert.ok(hintLine, `hint not on its own line:\n${out}`);
  // The hint sits under the check, never inline with it.
  assert.ok(!lines.some((l) => l.includes("keychain-telegram") && l.includes(telegram.hint!)));
  assert.ok(hintLine.startsWith(" "), "the hint line is not indented");
});

test("formatChecks on an empty list is the empty string", () => {
  assert.equal(formatChecks([]), "");
});

// ------------------------------------------------------ config short-circuit

test("an unloadable config yields exactly one failing check", () => {
  let laterDepCalled = false;
  const mark = (): never => {
    laterDepCalled = true;
    throw new Error("a later check dereferenced a missing config");
  };

  const checks = runDoctor({
    loadConfig: () => ({ ok: false, errors: ["mode is required", "projectDir is required"] }),
    pythonVersion: mark,
    isExecutableFile: mark,
    fileExists: mark,
    signalEntries: mark,
    keychainHas: mark,
    detectAgents: mark,
    jobStatus: mark,
    runProbe: mark,
    now: () => NOW,
  });

  assert.equal(checks.length, 1);
  assert.equal(checks[0].name, "config");
  assert.equal(checks[0].status, "fail");
  assert.ok(checks[0].detail.includes("mode is required"));
  assert.ok(checks[0].detail.includes("projectDir is required"));
  assert.equal(checks[0].hint, "run: kis-trader init");
  assert.equal(laterDepCalled, false);
  assert.equal(exitCodeFor(checks), 1);
});

// ------------------------------------------------------------ python / venv / db

test("an unsupported interpreter fails the python check with a re-detect hint", () => {
  const checks = runDoctor(healthy({ pythonVersion: () => [3, 14] }));
  const python = get(checks, "python");
  assert.equal(python.status, "fail");
  assert.ok(python.detail.includes("3.14"));
  assert.equal(python.hint, "re-run kis-trader init to re-detect Python");
  assert.equal(exitCodeFor(checks), 1);
});

test("an interpreter that does not answer fails the python check", () => {
  const checks = runDoctor(healthy({ pythonVersion: () => null }));
  const python = get(checks, "python");
  assert.equal(python.status, "fail");
  assert.ok(python.detail.includes(PYTHON));
});

test("a missing venv interpreter fails the venv check", () => {
  const checks = runDoctor(
    healthy({ isExecutableFile: (p) => p !== venvPython(PROJECT) }),
  );
  const venv = get(checks, "venv");
  assert.equal(venv.status, "fail");
  assert.ok(venv.detail.includes(venvPython(PROJECT)));
  assert.equal(venv.hint, "run: kis-trader init");
});

test("a missing database file fails the database check", () => {
  const seen: string[] = [];
  const checks = runDoctor(
    healthy({
      fileExists: (p) => {
        seen.push(p);
        return false;
      },
    }),
  );
  const db = get(checks, "database");
  assert.equal(db.status, "fail");
  assert.deepEqual(seen, [`${PROJECT}/data/trades.sqlite`]);
  assert.ok(db.detail.includes("trades.sqlite"));
  assert.ok(db.hint);
});

// -------------------------------------------------------------------- keychain

test("two of three KIS items present fails and names the missing account", () => {
  const missing = kisAccount("paper", "secret");
  const checks = runDoctor(
    healthy({ keychainHas: (_service, account) => account !== missing }),
  );

  const kis = get(checks, "keychain-kis");
  assert.equal(kis.status, "fail");
  assert.ok(kis.detail.includes("paper-secret"), kis.detail);
  assert.ok(!kis.detail.includes("paper-appkey"), kis.detail);
  assert.equal(kis.hint, "run: kis-trader init");
  assert.equal(exitCodeFor(checks), 1);
});

test("no KIS items at all fails and names all three accounts", () => {
  const checks = runDoctor(healthy({ keychainHas: () => false }));
  const kis = get(checks, "keychain-kis");
  assert.equal(kis.status, "fail");
  for (const kind of ["appkey", "secret", "account"] as const) {
    assert.ok(kis.detail.includes(kisAccount("paper", kind)), kis.detail);
  }
});

test("the KIS accounts probed follow cfg.mode", () => {
  const asked: string[] = [];
  runDoctor(
    healthy(
      {
        keychainHas: (_service, account) => {
          asked.push(account);
          return true;
        },
      },
      cfg({ mode: "real" }),
    ),
  );
  assert.ok(asked.includes("real-appkey"), asked.join(","));
  assert.ok(asked.includes("real-secret"));
  assert.ok(asked.includes("real-account"));
  assert.ok(!asked.some((a) => a.startsWith("paper-")));
});

test("a locked keychain fails the check instead of crashing", () => {
  const checks = runDoctor(
    healthy({
      keychainHas: () => {
        throw new KeychainLockedError();
      },
    }),
  );
  const kis = get(checks, "keychain-kis");
  assert.equal(kis.status, "fail");
  assert.ok(kis.detail.includes("-25308"), kis.detail);
  assert.ok(kis.hint?.toLowerCase().includes("unlock"), kis.hint);
  // The run still completes every later check.
  assert.equal(get(checks, `job:${JOB_KEYS[0]}`).status, "pass");
});

test("neither Telegram item present is a warn, and warns do not fail the command", () => {
  const checks = runDoctor(healthy({ keychainHas: (service) => service === KIS_SERVICE }));
  const telegram = get(checks, "keychain-telegram");
  assert.equal(telegram.status, "warn");
  assert.ok(telegram.hint);
  assert.equal(exitCodeFor(checks), 0);
});

test("exactly one Telegram item present is a fail", () => {
  const checks = runDoctor(
    healthy({
      keychainHas: (service, account) =>
        service === KIS_SERVICE || account === "stock-trader",
    }),
  );
  const telegram = get(checks, "keychain-telegram");
  assert.equal(telegram.status, "fail");
  assert.ok(telegram.detail.includes("stock-trader-chatid"), telegram.detail);
  assert.equal(exitCodeFor(checks), 1);
});

// ------------------------------------------------------------------ llm-agent

test("the configured agent being absent while another exists is a warn", () => {
  const checks = runDoctor(
    healthy({
      detectAgents: () => ({
        claude: null,
        codex: { path: "/usr/local/bin/codex", version: null },
        pi: null,
        gemini: null,
      }),
    }),
  );
  const agent = get(checks, "llm-agent");
  assert.equal(agent.status, "warn");
  assert.ok(agent.detail.includes("codex"), agent.detail);
  assert.ok(agent.hint?.includes("codex"), agent.hint);
  assert.equal(exitCodeFor(checks), 0);
});

test("no agent at all is a fail", () => {
  const checks = runDoctor(
    healthy({
      detectAgents: () => ({ claude: null, codex: null, pi: null, gemini: null }),
    }),
  );
  const agent = get(checks, "llm-agent");
  assert.equal(agent.status, "fail");
  assert.ok(agent.hint);
});

test("a detected agent reports its path", () => {
  const checks = runDoctor(healthy());
  assert.ok(get(checks, "llm-agent").detail.includes("/usr/local/bin/claude"));
});

// ----------------------------------------------------------------- signal-dir

test("a missing signal directory fails and names it plus stock-signal-bot", () => {
  const checks = runDoctor(healthy({ signalEntries: () => null }));
  const dir = get(checks, "signal-dir");
  assert.equal(dir.status, "fail");
  assert.ok(dir.hint?.includes(SIGNAL_DIR), dir.hint);
  assert.ok(dir.hint?.includes("stock-signal-bot"), dir.hint);
  assert.equal(exitCodeFor(checks), 1);
});

test("an existing but empty signal directory is a warn, not a fail", () => {
  const checks = runDoctor(healthy({ signalEntries: () => [] }));
  const dir = get(checks, "signal-dir");
  assert.equal(dir.status, "warn");
  assert.ok(dir.hint);
  assert.equal(exitCodeFor(checks), 0);
});

test("a signal 71 h old passes and 73 h old warns, judged by the injected clock", () => {
  const at = (hours: number): Check => {
    const checks = runDoctor(
      healthy({
        signalEntries: () => [{ name: "kospi.json", mtimeMs: NOW - hours * HOUR }],
        now: () => NOW,
      }),
    );
    return get(checks, "signal-dir");
  };

  const fresh = at(71);
  assert.equal(fresh.status, "pass");
  assert.ok(fresh.detail.includes("71h"), fresh.detail);

  const stale = at(73);
  assert.equal(stale.status, "warn");
  assert.ok(stale.detail.includes("73h"), stale.detail);
  assert.ok(stale.hint);
});

test("the newest file decides freshness even when older ones sit beside it", () => {
  const checks = runDoctor(
    healthy({
      signalEntries: () => [
        { name: "old.json", mtimeMs: NOW - 200 * HOUR },
        { name: "new.json", mtimeMs: NOW - 1 * HOUR },
        { name: "mid.json", mtimeMs: NOW - 40 * HOUR },
      ],
    }),
  );
  const dir = get(checks, "signal-dir");
  assert.equal(dir.status, "pass");
  assert.ok(dir.detail.includes("1h"), dir.detail);
});

// -------------------------------------------------------------------- kis-api

test("a probe reporting ok:true passes and shows the masked account", () => {
  const probe = recordingProbe();
  const checks = runDoctor(healthy({ runProbe: probe.run }));

  const api = get(checks, "kis-api");
  assert.equal(api.status, "pass");
  assert.ok(api.detail.includes("****0180"), api.detail);
  assert.ok(api.detail.includes("paper"), api.detail);
  assert.equal(api.hint, undefined);

  assert.equal(probe.calls.length, 1);
  const call = probe.calls[0];
  assert.equal(call.command, venvPython(PROJECT));
  assert.deepEqual(call.args, ["-m", "src.broker.probe"]);
  assert.equal(call.opts.cwd, PROJECT);
  assert.equal(call.opts.timeoutMs, 15_000);
  assert.equal(call.opts.env.KIS_MODE, "paper");
  assert.equal(call.opts.env.PATH, process.env.PATH);
});

test("the probe inherits the configured mode", () => {
  const probe = recordingProbe();
  runDoctor(healthy({ runProbe: probe.run }, cfg({ mode: "real" })));
  assert.equal(probe.calls[0].opts.env.KIS_MODE, "real");
});

test("reason missing_credentials fails with an init hint", () => {
  const checks = runDoctor(
    healthy({
      runProbe: () => ({
        code: 1,
        stdout: probeJson({ ok: false, reason: "missing_credentials", detail: "자격증명 누락: KIS_APP_KEY" }),
        stderr: "",
      }),
    }),
  );
  const api = get(checks, "kis-api");
  assert.equal(api.status, "fail");
  assert.equal(api.hint, "run: kis-trader init");
  assert.equal(exitCodeFor(checks), 1);
});

test("reason auth_failed fails and blames the app key/secret", () => {
  const checks = runDoctor(
    healthy({
      runProbe: () => ({
        code: 1,
        stdout: probeJson({ ok: false, reason: "auth_failed", detail: "token 발급 실패" }),
        stderr: "",
      }),
    }),
  );
  const api = get(checks, "kis-api");
  assert.equal(api.status, "fail");
  assert.equal(api.hint, "app key/secret rejected — re-run kis-trader init");
  assert.equal(exitCodeFor(checks), 1);
});

test("reason rate_limited warns about throttling, never about credentials", () => {
  const checks = runDoctor(
    healthy({
      runProbe: () => ({
        code: 1,
        stdout: probeJson({ ok: false, reason: "rate_limited", detail: "초당 거래건수를 초과" }),
        stderr: "",
      }),
    }),
  );
  const api = get(checks, "kis-api");
  assert.equal(api.status, "warn");
  assert.ok(/throttl/i.test(api.detail), api.detail);
  assert.ok(!/credential/i.test(api.detail) || /not/i.test(api.detail), api.detail);
  assert.ok(api.hint);
  assert.equal(exitCodeFor(checks), 0);
});

test("reason network warns and points at the KIS host", () => {
  const checks = runDoctor(
    healthy({
      runProbe: () => ({
        code: 1,
        stdout: probeJson({ ok: false, reason: "network", detail: "Connection timed out" }),
        stderr: "",
      }),
    }),
  );
  const api = get(checks, "kis-api");
  assert.equal(api.status, "warn");
  assert.equal(api.hint, "check connectivity to openapivts.koreainvestment.com");
  assert.equal(exitCodeFor(checks), 0);
});

test("unparseable probe output fails with the first 200 chars of stderr", () => {
  const noise = "T".repeat(500);
  const checks = runDoctor(
    healthy({
      runProbe: () => ({ code: 1, stdout: "ModuleNotFoundError\n", stderr: noise }),
    }),
  );
  const api = get(checks, "kis-api");
  assert.equal(api.status, "fail");
  assert.equal(api.detail, "T".repeat(200));
  assert.ok(api.hint);
  assert.equal(exitCodeFor(checks), 1);
});

test("an unknown reason slug fails rather than passing silently", () => {
  const checks = runDoctor(
    healthy({
      runProbe: () => ({
        code: 1,
        stdout: probeJson({ ok: false, reason: "rejected", detail: "잔고 조회가 응답을 반환하지 않았습니다" }),
        stderr: "",
      }),
    }),
  );
  const api = get(checks, "kis-api");
  assert.equal(api.status, "fail");
  assert.notEqual(api.detail.trim(), "");
});

test("a probe that produced nothing at all still yields a non-empty detail", () => {
  const checks = runDoctor(
    healthy({ runProbe: () => ({ code: -1, stdout: "", stderr: "" }) }),
  );
  const api = get(checks, "kis-api");
  assert.equal(api.status, "fail");
  assert.notEqual(api.detail.trim(), "");
});

test("a failed venv check skips the probe entirely", () => {
  const probe = recordingProbe();
  const checks = runDoctor(
    healthy({
      isExecutableFile: (p) => p !== venvPython(PROJECT),
      runProbe: probe.run,
    }),
  );

  const api = get(checks, "kis-api");
  assert.equal(api.status, "warn");
  assert.equal(api.detail, "skipped — venv missing");
  assert.ok(api.hint);
  assert.equal(probe.calls.length, 0);
});

// ----------------------------------------------------------------------- jobs

test("an enabled job that is absent fails and sets the exit code", () => {
  const checks = runDoctor(
    healthy({ jobStatus: (job) => (job === "monitor" ? "absent" : "loaded") }),
    );
  const job = get(checks, "job:monitor");
  assert.equal(job.status, "fail");
  assert.ok(job.hint);
  assert.equal(exitCodeFor(checks), 1);
});

test("a disabled job that is absent is only a warn", () => {
  const checks = runDoctor(
    healthy(
      { jobStatus: (job) => (job === "dipBuy" ? "absent" : "loaded") },
      cfg({
        jobs: {
          orchestrator: true,
          monitor: true,
          reconciler: true,
          dipBuy: false,
          usOrchestrator: true,
        },
      }),
    ),
  );
  const job = get(checks, "job:dipBuy");
  assert.equal(job.status, "warn");
  assert.ok(job.hint);
  assert.equal(exitCodeFor(checks), 0);
});

test("an installed-but-unloaded job warns with a bootstrap command", () => {
  const checks = runDoctor(healthy({ jobStatus: () => "installed-not-loaded" }));
  const job = get(checks, "job:orchestrator");
  assert.equal(job.status, "warn");
  assert.ok(job.hint?.startsWith("launchctl bootstrap gui/$UID "), job.hint);
  assert.ok(job.hint?.endsWith(".plist"), job.hint);
  assert.equal(exitCodeFor(checks), 0);
});

test("every job key gets its own check", () => {
  const asked: string[] = [];
  const checks = runDoctor(
    healthy({
      jobStatus: (job) => {
        asked.push(job);
        return "loaded";
      },
    }),
  );
  assert.deepEqual(asked, [...JOB_KEYS]);
  assert.deepEqual(
    checks.filter((c) => c.name.startsWith("job:")).map((c) => c.name),
    JOB_KEYS.map((k) => `job:${k}`),
  );
});

// ------------------------------------------------------------------ exit code

test("exitCodeFor is 1 only when something failed", () => {
  assert.equal(exitCodeFor([]), 0);
  assert.equal(exitCodeFor([{ name: "a", status: "pass", detail: "" }]), 0);
  assert.equal(
    exitCodeFor([
      { name: "a", status: "pass", detail: "" },
      { name: "b", status: "warn", detail: "", hint: "h" },
    ]),
    0,
  );
  assert.equal(
    exitCodeFor([
      { name: "a", status: "warn", detail: "", hint: "h" },
      { name: "b", status: "fail", detail: "", hint: "h" },
    ]),
    1,
  );
});
