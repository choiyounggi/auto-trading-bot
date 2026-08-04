import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { homedir, userInfo } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { JOB_KEYS, type Config } from "../config.js";
import {
  JOBS,
  installJob,
  jobStatus,
  labelFor,
  plistPath,
  renderPlist,
  uninstallJob,
  type JobKey,
  type LaunchctlRunner,
} from "../launchd.js";

/**
 * Nothing in this file may touch the machine it runs on: no `launchctl` is ever
 * spawned (every call goes through an injected runner), and every path that
 * would land in `~/Library/LaunchAgents` is redirected by pointing `HOME` at a
 * scratch dir under the repo — `os.homedir()` reads `$HOME` on POSIX, so
 * `plistPath()` follows it without needing a test-only parameter.
 *
 * Scratch lives in `dist-test/.tmp` (inside the repo, gitignored) rather than
 * the system temp dir, per the project's security policy.
 */
const SCRATCH_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", ".tmp");

const UID = process.getuid?.() ?? 0;

const CFG: Config = {
  mode: "paper",
  projectDir: "/Users/alice/auto-trading-bot",
  pythonPath: "/abs/python3.11",
  signalDir: "/Users/alice/.kis-trader/signals",
  llmAgent: "claude",
  jobs: {
    orchestrator: true,
    monitor: true,
    reconciler: true,
    dipBuy: true,
    usOrchestrator: true,
  },
};

/** A KIS_TRADER_HOME used only as an interpolated string (never written to). */
const HOME_STR = "/kis-home";

/**
 * Run `fn` with `HOME` (and therefore `os.homedir()`) pointed at a fresh scratch
 * directory, restoring the real value afterwards. `dir` is that fake home;
 * `state` is a separate KIS_TRADER_HOME the job would write logs into.
 */
function withHome(fn: (dir: string, state: string) => void): void {
  mkdirSync(SCRATCH_ROOT, { recursive: true });
  const dir = mkdtempSync(join(SCRATCH_ROOT, "launchd-"));
  const state = join(dir, "state");
  const realHome = process.env.HOME;
  process.env.HOME = dir;
  try {
    assert.equal(homedir(), dir, "guard: HOME override must take effect");
    fn(dir, state);
  } finally {
    if (realHome === undefined) delete process.env.HOME;
    else process.env.HOME = realHome;
    rmSync(dir, { recursive: true, force: true });
  }
}

/** A `launchctl` runner stub that records every argv it was handed. */
function stub(
  reply: (args: string[]) => string,
): LaunchctlRunner & { calls: string[][] } {
  const calls: string[][] = [];
  const fn = (args: string[]): string => {
    calls.push(args);
    return reply(args);
  };
  return Object.assign(fn, { calls });
}

/** Every subcommand succeeds silently; `list` answers with `output`. */
function listing(output: string): LaunchctlRunner & { calls: string[][] } {
  return stub((args) => (args[0] === "list" ? output : ""));
}

/** A realistic `launchctl list` block containing `labels`. */
function listOutput(...labels: string[]): string {
  const head = "PID\tStatus\tLabel\n-\t0\tcom.apple.SafariHistoryServiceAgent\n";
  return head + labels.map((l) => `-\t0\t${l}\n`).join("");
}

function count(haystack: string, needle: string): number {
  return haystack.split(needle).length - 1;
}

/** Reject anything `plutil` will not parse — proves the escaping is honest. */
function assertParses(xml: string): void {
  if (process.platform !== "darwin") return;
  const out = execFileSync("plutil", ["-lint", "-"], {
    input: xml,
    encoding: "utf8",
    stdio: ["pipe", "pipe", "pipe"],
  });
  assert.match(out, /OK/, `plutil rejected the plist:\n${out}`);
}

// ── JOBS inventory ────────────────────────────────────────────────────

test("JOBS covers exactly the job inventory in config.ts", () => {
  assert.deepEqual([...Object.keys(JOBS)].sort(), [...JOB_KEYS].sort());
});

test("JOBS preserves the installed schedules and module invocations", () => {
  assert.deepEqual(JOBS.orchestrator.args, ["-m", "src.orchestrator", "--carry-over"]);
  assert.deepEqual(JOBS.orchestrator.schedule, { hour: 9, minute: 5 });

  assert.deepEqual(JOBS.dipBuy.args, ["-m", "src.orchestrator", "--dip-only"]);
  assert.deepEqual(JOBS.dipBuy.schedule, { hour: 15, minute: 0 });

  assert.deepEqual(JOBS.reconciler.args, ["-m", "src.reconciler"]);
  assert.deepEqual(JOBS.reconciler.schedule, { hour: 16, minute: 0 });

  assert.deepEqual(JOBS.monitor.args, ["-m", "src.monitor"]);
  assert.deepEqual(JOBS.monitor.schedule, { intervalSec: 300 });

  assert.deepEqual(JOBS.usOrchestrator.args, [
    "-m",
    "src.orchestrator",
    "--asset-class",
    "overseas_stock",
  ]);
  assert.deepEqual(JOBS.usOrchestrator.schedule, { hour: 22, minute: 45 });
});

test("no JOBS entry names an interpreter — the config supplies it", () => {
  for (const key of JOB_KEYS) {
    const spec = JOBS[key];
    assert.equal(spec.args[0], "-m", `${key} must be a module invocation`);
    assert.ok(!spec.args.some((a) => a.includes("python")), `${key} hardcodes a python`);
    assert.ok(!spec.log.includes("/"), `${key}.log must be a basename`);
  }
});

// ── labelFor / plistPath ──────────────────────────────────────────────

test("labelFor builds com.<username>.kistrader.<job>", () => {
  assert.equal(labelFor("dipBuy", "alice"), "com.alice.kistrader.dipBuy");
  assert.equal(labelFor("usOrchestrator", "alice"), "com.alice.kistrader.usOrchestrator");
});

test("labelFor defaults to the current username", () => {
  assert.equal(labelFor("monitor"), `com.${userInfo().username}.kistrader.monitor`);
});

test("plistPath lands in ~/Library/LaunchAgents", () => {
  withHome((dir) => {
    assert.equal(
      plistPath("com.alice.kistrader.monitor"),
      join(dir, "Library", "LaunchAgents", "com.alice.kistrader.monitor.plist"),
    );
  });
});

// ── renderPlist: normal ───────────────────────────────────────────────

test("renderPlist(orchestrator) writes the absolute interpreter, args and signal dir", () => {
  const out = renderPlist("orchestrator", CFG, HOME_STR, "alice");

  assert.ok(out.startsWith('<?xml version="1.0" encoding="UTF-8"?>\n'));
  assert.ok(out.includes("<!DOCTYPE plist PUBLIC"));
  assert.ok(out.trimEnd().endsWith("</plist>"));

  assert.ok(out.includes("<string>com.alice.kistrader.orchestrator</string>"));
  assert.ok(out.includes("<string>/abs/python3.11</string>"), "absolute interpreter");
  assert.ok(!/<string>python3(\.\d+)?<\/string>/.test(out), "no bare python3");
  assert.ok(out.includes("<string>--carry-over</string>"));
  assert.ok(out.includes("<string>src.orchestrator</string>"));
  assert.ok(out.includes(`<string>${CFG.projectDir}</string>`), "WorkingDirectory");
  assert.ok(out.includes("<key>KIS_TRADER_SIGNAL_DIR</key>"));
  assert.ok(out.includes(`<string>${CFG.signalDir}</string>`));
});

test("renderPlist(orchestrator) emits exactly five weekday dicts at 09:05", () => {
  const out = renderPlist("orchestrator", CFG, HOME_STR, "alice");

  assert.equal(count(out, "<key>Weekday</key>"), 5);
  assert.ok(out.includes("<key>StartCalendarInterval</key>"));
  assert.ok(!out.includes("<key>StartInterval</key>"), "a calendar job has no StartInterval");
  for (const day of [1, 2, 3, 4, 5]) {
    assert.ok(
      out.includes(
        `<dict><key>Weekday</key><integer>${day}</integer>` +
          `<key>Hour</key><integer>9</integer>` +
          `<key>Minute</key><integer>5</integer></dict>`,
      ),
      `weekday ${day} at 09:05 missing`,
    );
  }
  assert.ok(!out.includes("<integer>0</integer>"), "Sunday must not be scheduled");
  assert.ok(!out.includes("<integer>6</integer>"), "Saturday must not be scheduled");
});

test("renderPlist declares the full launchd environment", () => {
  const out = renderPlist("orchestrator", CFG, HOME_STR, "alice");

  for (const key of [
    "PATH",
    "HOME",
    "KIS_TRADER_HOME",
    "KIS_MODE",
    "KIS_TRADER_SIGNAL_DIR",
  ]) {
    assert.ok(out.includes(`<key>${key}</key>`), `${key} missing from the plist env`);
  }
  assert.ok(out.includes("<key>KIS_TRADER_HOME</key><string>/kis-home</string>"));
  assert.ok(out.includes("<key>KIS_MODE</key><string>paper</string>"));
  assert.ok(
    out.includes(`<key>HOME</key><string>${homedir()}</string>`),
    "HOME must be the real login home, not KIS_TRADER_HOME",
  );
});

test("renderPlist puts the interpreter's own directory first in PATH", () => {
  const out = renderPlist(
    "orchestrator",
    { ...CFG, pythonPath: "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11" },
    HOME_STR,
    "alice",
  );
  assert.ok(
    out.includes(
      "<key>PATH</key><string>/Library/Frameworks/Python.framework/Versions/3.11/bin:" +
        "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>",
    ),
    `PATH not as expected:\n${out}`,
  );
});

test("renderPlist dedupes the interpreter directory already present in PATH", () => {
  const out = renderPlist(
    "orchestrator",
    { ...CFG, pythonPath: "/opt/homebrew/bin/python3.12" },
    HOME_STR,
    "alice",
  );
  assert.ok(
    out.includes(
      "<key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>",
    ),
    `PATH not deduped:\n${out}`,
  );
  assert.equal(count(out, "/opt/homebrew/bin:"), 1);
});

test("renderPlist routes stdout and stderr to separate files under the state home", () => {
  const out = renderPlist("orchestrator", CFG, HOME_STR, "alice");
  assert.ok(out.includes("<key>StandardOutPath</key>"));
  assert.ok(out.includes("<string>/kis-home/logs/orchestrator.log</string>"));
  assert.ok(out.includes("<key>StandardErrorPath</key>"));
  assert.ok(out.includes("<string>/kis-home/logs/orchestrator.err.log</string>"));
});

test("renderPlist never sets KeepAlive on a one-shot job", () => {
  for (const key of JOB_KEYS) {
    const out = renderPlist(key, CFG, HOME_STR, "alice");
    assert.ok(!out.includes("KeepAlive"), `${key} must not be restarted in a loop`);
  }
});

test("renderPlist(monitor) uses StartInterval and no calendar schedule", () => {
  const out = renderPlist("monitor", CFG, HOME_STR, "alice");
  assert.ok(out.includes("<key>StartInterval</key>"));
  assert.ok(out.includes("<integer>300</integer>"));
  assert.ok(!out.includes("StartCalendarInterval"), "monitor is interval-driven");
  assert.equal(count(out, "<key>Weekday</key>"), 0);
  assert.ok(!out.includes("KeepAlive"), "StartInterval already re-runs it");
});

test("renderPlist(usOrchestrator) schedules 22:45 with the overseas asset class", () => {
  const out = renderPlist("usOrchestrator", CFG, HOME_STR, "alice");
  assert.ok(out.includes("<string>--asset-class</string>"));
  assert.ok(out.includes("<string>overseas_stock</string>"));
  assert.equal(count(out, "<key>Weekday</key>"), 5);
  assert.ok(
    out.includes("<key>Hour</key><integer>22</integer><key>Minute</key><integer>45</integer>"),
  );
});

test("every rendered job is a plist plutil accepts", () => {
  for (const key of JOB_KEYS) {
    assertParses(renderPlist(key, CFG, HOME_STR, "alice"));
  }
});

// ── renderPlist: boundary (escaping) ──────────────────────────────────

test("renderPlist escapes & < > in interpolated values and still parses", () => {
  const cfg: Config = {
    ...CFG,
    projectDir: "/Users/alice/r&d",
    signalDir: "/Users/alice/<sig>",
  };
  const out = renderPlist("orchestrator", cfg, "/kis&home", "a<b>c");

  assert.ok(out.includes("<string>/Users/alice/r&amp;d</string>"));
  assert.ok(out.includes("<string>/Users/alice/&lt;sig&gt;</string>"));
  assert.ok(out.includes("<string>com.a&lt;b&gt;c.kistrader.orchestrator</string>"));
  assert.ok(out.includes("/kis&amp;home/logs/orchestrator.log"));

  // No bare `&` survives: every one in the output opens an entity.
  for (const amp of out.matchAll(/&(?!amp;|lt;|gt;)/g)) {
    assert.fail(`unescaped & at offset ${amp.index}`);
  }
  assertParses(out);
});

test("renderPlist tolerates empty-ish interpolated values without breaking the XML", () => {
  const out = renderPlist("monitor", { ...CFG, signalDir: "" }, "", "");
  assert.ok(out.includes("<key>KIS_TRADER_SIGNAL_DIR</key><string></string>"));
  assert.ok(out.includes("<string>com..kistrader.monitor</string>"));
  assertParses(out);
});

// ── installJob: normal ────────────────────────────────────────────────

test("installJob writes the plist 0644, creates the log dir, then observes the label", () => {
  withHome((dir, state) => {
    const label = labelFor("orchestrator");
    const run = listing(listOutput(label));

    const res = installJob("orchestrator", CFG, state, run);

    assert.equal(res.label, label);
    assert.equal(res.path, join(dir, "Library", "LaunchAgents", `${label}.plist`));
    assert.equal(res.loaded, true, res.message);
    assert.ok(existsSync(res.path));
    assert.equal(statSync(res.path).mode & 0o777, 0o644);
    assert.ok(existsSync(join(state, "logs")), "log directory must exist before the job runs");

    assert.deepEqual(run.calls, [
      ["bootout", `gui/${UID}/${label}`],
      ["bootstrap", `gui/${UID}`, res.path],
      ["list"],
    ]);
  });
});

test("installJob overwrites a stale plist and re-tightens its mode", () => {
  withHome((dir, state) => {
    const label = labelFor("monitor");
    const path = join(dir, "Library", "LaunchAgents", `${label}.plist`);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, "stale\n", { mode: 0o666 });

    const res = installJob("monitor", CFG, state, listing(listOutput(label)));

    assert.equal(res.loaded, true, res.message);
    assert.equal(statSync(path).mode & 0o777, 0o644, "a loose mode must be re-tightened");
    assert.equal(readFileSync(path, "utf8"), renderPlist("monitor", CFG, state));
  });
});

test("installJob retries the bootstrap after a successful bootout freed the slot", () => {
  withHome((_dir, state) => {
    const label = labelFor("reconciler");
    let bootstraps = 0;
    const run = stub((args) => {
      if (args[0] === "list") return listOutput(label);
      if (args[0] === "bootstrap") {
        bootstraps += 1;
        if (bootstraps < 3) throw new Error("Bootstrap failed: 37: Operation already in progress");
        return "";
      }
      return "";
    });

    const res = installJob("reconciler", CFG, state, run);

    assert.equal(bootstraps, 3, "the slot is not free the instant bootout returns");
    assert.equal(res.loaded, true, res.message);
  });
});

// ── installJob: error ─────────────────────────────────────────────────

test("installJob reports loaded:false when the label is absent from launchctl list", () => {
  withHome((_dir, state) => {
    // Every launchctl call exits 0 — only the observation says otherwise.
    const run = listing(listOutput("com.other.kistrader.orchestrator"));

    const res = installJob("orchestrator", CFG, state, run);

    assert.equal(res.loaded, false, "loaded must come from observation, not the exit code");
    assert.ok(res.message.includes(res.label));
    assert.ok(res.message.includes("launchctl list"), res.message);
    assert.ok(existsSync(res.path), "the plist is still on disk for the user to inspect");
  });
});

test("installJob returns loaded:false with the failure text when bootstrap always throws", () => {
  withHome((_dir, state) => {
    const run = stub(() => {
      throw new Error("Bootstrap failed: 5: Input/output error");
    });

    let res: ReturnType<typeof installJob> | undefined;
    assert.doesNotThrow(() => {
      res = installJob("dipBuy", CFG, state, run);
    });

    assert.equal(res?.loaded, false);
    assert.ok(res?.message.includes("bootstrap"), res?.message);
    assert.ok(res?.message.includes("Input/output error"), res?.message);
    assert.ok(existsSync(res!.path));
  });
});

test("installJob does not retry the bootstrap when nothing was booted out", () => {
  withHome((_dir, state) => {
    const run = stub((args) => {
      if (args[0] === "bootout") throw new Error("Boot-out failed: 3: No such process");
      if (args[0] === "bootstrap") throw new Error("Bootstrap failed: 5: Input/output error");
      return "";
    });

    const res = installJob("dipBuy", CFG, state, run);

    assert.equal(res.loaded, false);
    assert.equal(
      run.calls.filter((c) => c[0] === "bootstrap").length,
      1,
      "no slot was occupied, so retrying buys nothing",
    );
  });
});

test("installJob gives up after three bootstrap attempts", () => {
  withHome((_dir, state) => {
    const run = stub((args) => {
      if (args[0] === "bootstrap") throw new Error("Bootstrap failed: 37: Operation in progress");
      if (args[0] === "list") return listOutput();
      return "";
    });

    const res = installJob("usOrchestrator", CFG, state, run);

    assert.equal(run.calls.filter((c) => c[0] === "bootstrap").length, 3);
    assert.equal(res.loaded, false);
  });
});

test("installJob survives a launchctl list that itself fails", () => {
  withHome((_dir, state) => {
    const run = stub((args) => {
      if (args[0] === "list") throw new Error("launchctl: Couldn't talk to launchd");
      return "";
    });

    let res: ReturnType<typeof installJob> | undefined;
    assert.doesNotThrow(() => {
      res = installJob("monitor", CFG, state, run);
    });
    assert.equal(res?.loaded, false);
    assert.ok(res!.message.length > 0);
  });
});

// ── uninstallJob ──────────────────────────────────────────────────────

test("uninstallJob boots the job out and unlinks its plist", () => {
  withHome((_dir, state) => {
    const label = labelFor("monitor");
    const run = listing(listOutput(label));
    const path = installJob("monitor", CFG, state, run).path;
    run.calls.length = 0;

    const res = uninstallJob("monitor", run);

    assert.equal(res.label, label);
    assert.equal(res.path, path);
    assert.equal(res.removed, true, res.message);
    assert.equal(existsSync(path), false);
    assert.deepEqual(run.calls, [["bootout", `gui/${UID}/${label}`]]);
  });
});

test("uninstallJob is a no-op that reports removed:false when no plist exists", () => {
  withHome(() => {
    const run = listing("");
    const res = uninstallJob("reconciler", run);

    assert.equal(res.removed, false);
    assert.ok(res.message.includes(res.path), res.message);
    assert.equal(existsSync(res.path), false);
  });
});

test("uninstallJob still removes the plist when the bootout fails", () => {
  withHome((dir) => {
    const label = labelFor("dipBuy");
    const path = join(dir, "Library", "LaunchAgents", `${label}.plist`);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, "<plist/>\n");

    const run = stub((args) => {
      if (args[0] === "bootout") throw new Error("Boot-out failed: 3: No such process");
      return "";
    });

    let res: ReturnType<typeof uninstallJob> | undefined;
    assert.doesNotThrow(() => {
      res = uninstallJob("dipBuy", run);
    });

    assert.equal(res?.removed, true, res?.message);
    assert.equal(existsSync(path), false);
  });
});

// ── jobStatus ─────────────────────────────────────────────────────────

test("jobStatus reports absent when no plist is installed", () => {
  withHome(() => {
    const run = stub(() => {
      throw new Error("launchctl must not be consulted for an absent job");
    });
    assert.equal(jobStatus("orchestrator", run), "absent");
    assert.deepEqual(run.calls, []);
  });
});

test("jobStatus reports installed-not-loaded when the label is missing from the list", () => {
  withHome((_dir, state) => {
    installJob("orchestrator", CFG, state, listing(""));
    assert.equal(
      jobStatus("orchestrator", listing(listOutput("com.other.kistrader.orchestrator"))),
      "installed-not-loaded",
    );
  });
});

test("jobStatus reports loaded when the label appears in the list", () => {
  withHome((_dir, state) => {
    const label = labelFor("monitor");
    installJob("monitor", CFG, state, listing(listOutput(label)));
    assert.equal(jobStatus("monitor", listing(listOutput(label))), "loaded");
  });
});

test("jobStatus does not mistake a longer label that merely contains ours", () => {
  withHome((_dir, state) => {
    const label = labelFor("monitor");
    installJob("monitor", CFG, state, listing(""));
    assert.equal(
      jobStatus("monitor", listing(listOutput(`${label}.backup`))),
      "installed-not-loaded",
    );
  });
});

test("jobStatus falls back to installed-not-loaded when launchctl list fails", () => {
  withHome((_dir, state) => {
    installJob("reconciler", CFG, state, listing(""));
    const run = stub(() => {
      throw new Error("launchctl: Couldn't talk to launchd");
    });

    let status: ReturnType<typeof jobStatus> | undefined;
    assert.doesNotThrow(() => {
      status = jobStatus("reconciler", run);
    });
    assert.equal(status, "installed-not-loaded");
  });
});

test("jobStatus answers for every job key without touching the machine", () => {
  withHome(() => {
    const run = listing("");
    for (const key of JOB_KEYS) {
      const status: string = jobStatus(key as JobKey, run);
      assert.equal(status, "absent");
    }
  });
});
