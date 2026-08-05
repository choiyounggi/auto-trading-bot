/**
 * launchd LaunchAgents for the five trading jobs: render, install, uninstall,
 * status.
 *
 * The plists are rendered at runtime from the user's config rather than shipped
 * as files, because every path in them belongs to the machine the CLI is
 * installed on — a committed plist carries the author's home directory and is
 * wrong everywhere else.
 *
 * A launchd job gets a minimal environment and loads no rc files, so nothing
 * here may rely on the user's shell: the interpreter is `cfg.pythonPath`, an
 * absolute path a version-manager shim cannot shadow, and every variable the
 * engine needs (`PATH`, `HOME`, `KIS_TRADER_HOME`, `KIS_MODE`,
 * `KIS_TRADER_SIGNAL_DIR`) is declared inside the plist. Both jobs' streams get
 * an explicit log file — a service without a log sink fails silently.
 *
 * Installation is confirmed by *observation*: `launchctl bootstrap` exiting 0
 * means the request was accepted, not that the job is loaded, so `installJob`
 * only reports `loaded: true` when the label actually shows up in
 * `launchctl list`.
 *
 * No job carries `KeepAlive`. These are one-shot runs on a schedule; KeepAlive
 * would make launchd restart them the moment they finish.
 */

import { execFileSync } from "node:child_process";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { homedir, userInfo } from "node:os";
import { dirname, join } from "node:path";

import { JOB_KEYS, type Config, type JobName } from "./config.js";

/** The job inventory lives in `config.ts`; this is the same union, re-exported. */
export type JobKey = JobName;

/** `launchctl` is bounded so a wedged launchd fails the command instead of hanging it. */
const TIMEOUT_MS = 10_000;

/**
 * `bootout` returns before launchd has actually released the service slot, so a
 * `bootstrap` issued immediately after can fail with "Operation already in
 * progress". Retry a few times, spaced out.
 */
const BOOTSTRAP_ATTEMPTS = 3;
const BOOTSTRAP_PAUSE_MS = 700;

/** PATH entries appended after the interpreter's own directory. */
const BASE_PATH = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"];

export interface JobSpec {
  /** Arguments after the interpreter — always a `-m module` invocation. */
  args: string[];
  schedule: { hour: number; minute: number } | { intervalSec: number };
  /** Basename of the stdout log; stderr gets the `.err.log` sibling. */
  log: string;
}

/**
 * The five jobs, with the schedules the previously hand-installed plists ran on.
 * Keyed by `JobName`, so a job added to `config.ts` without a schedule here is a
 * type error rather than a job that silently never installs.
 */
export const JOBS: Record<JobKey, JobSpec> = {
  orchestrator: {
    args: ["-m", "src.orchestrator", "--carry-over"],
    schedule: { hour: 9, minute: 5 },
    log: "orchestrator.log",
  },
  monitor: {
    args: ["-m", "src.monitor"],
    schedule: { intervalSec: 300 },
    log: "monitor.log",
  },
  reconciler: {
    args: ["-m", "src.reconciler"],
    schedule: { hour: 16, minute: 0 },
    log: "reconciler.log",
  },
  dipBuy: {
    args: ["-m", "src.orchestrator", "--dip-only"],
    schedule: { hour: 15, minute: 0 },
    log: "dipBuy.log",
  },
  usOrchestrator: {
    args: ["-m", "src.orchestrator", "--asset-class", "overseas_stock"],
    schedule: { hour: 22, minute: 45 },
    log: "usOrchestrator.log",
  },
};

/** Runs `launchctl` with `args` and returns its stdout; throws on failure. */
export type LaunchctlRunner = (args: string[]) => string;

const defaultRun: LaunchctlRunner = (args) =>
  execFileSync("launchctl", args, {
    encoding: "utf8",
    timeout: TIMEOUT_MS,
    stdio: ["ignore", "pipe", "pipe"],
  });

export function labelFor(job: JobKey, username = userInfo().username): string {
  return `com.${username}.kistrader.${job}`;
}

export function plistPath(label: string): string {
  return join(homedir(), "Library", "LaunchAgents", `${label}.plist`);
}

/** XML text escaping. `&` first, or it would double-escape the entities below. */
function esc(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function stdoutPath(job: JobKey, home: string): string {
  return join(home, "logs", JOBS[job].log);
}

function stderrPath(job: JobKey, home: string): string {
  return join(home, "logs", JOBS[job].log.replace(/\.log$/, "") + ".err.log");
}

/** The interpreter's own directory first, then the usual prefixes, deduped. */
function pathValue(pythonPath: string): string {
  const entries = [dirname(pythonPath), ...BASE_PATH];
  return [...new Set(entries)].join(":");
}

function scheduleXml(job: JobKey): string {
  const schedule = JOBS[job].schedule;
  if ("intervalSec" in schedule) {
    return `  <key>StartInterval</key>\n  <integer>${schedule.intervalSec}</integer>\n`;
  }
  // Weekday 1–5 is Monday–Friday: the markets these jobs trade are closed on
  // weekends, and launchd has no "weekdays" shorthand.
  const days = [1, 2, 3, 4, 5]
    .map(
      (day) =>
        `    <dict><key>Weekday</key><integer>${day}</integer>` +
        `<key>Hour</key><integer>${schedule.hour}</integer>` +
        `<key>Minute</key><integer>${schedule.minute}</integer></dict>\n`,
    )
    .join("");
  return `  <key>StartCalendarInterval</key>\n  <array>\n${days}  </array>\n`;
}

/**
 * The plist text for `job`. `home` is the state directory (`KIS_TRADER_HOME`)
 * the logs live under; `username` only affects the label.
 */
export function renderPlist(
  job: JobKey,
  cfg: Config,
  home: string,
  username?: string,
): string {
  const label = labelFor(job, username);
  const program = [cfg.pythonPath, ...JOBS[job].args]
    .map((arg) => `    <string>${esc(arg)}</string>\n`)
    .join("");
  const env: [string, string][] = [
    ["PATH", pathValue(cfg.pythonPath)],
    ["HOME", homedir()],
    ["KIS_TRADER_HOME", home],
    ["KIS_MODE", cfg.mode],
    ["KIS_TRADER_SIGNAL_DIR", cfg.signalDir],
  ];
  const envXml = env
    .map(([k, v]) => `    <key>${k}</key><string>${esc(v)}</string>\n`)
    .join("");

  return (
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" ' +
    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n' +
    '<plist version="1.0">\n' +
    "<dict>\n" +
    `  <key>Label</key>\n  <string>${esc(label)}</string>\n` +
    `  <key>ProgramArguments</key>\n  <array>\n${program}  </array>\n` +
    `  <key>WorkingDirectory</key>\n  <string>${esc(cfg.projectDir)}</string>\n` +
    scheduleXml(job) +
    `  <key>StandardOutPath</key>\n  <string>${esc(stdoutPath(job, home))}</string>\n` +
    `  <key>StandardErrorPath</key>\n  <string>${esc(stderrPath(job, home))}</string>\n` +
    `  <key>EnvironmentVariables</key>\n  <dict>\n${envXml}  </dict>\n` +
    "</dict>\n" +
    "</plist>\n"
  );
}

/** The GUI domain of the calling user — where a LaunchAgent belongs. */
function guiDomain(): string {
  return `gui/${process.getuid?.() ?? 0}`;
}

function text(value: unknown): string {
  return value === undefined || value === null ? "" : String(value);
}

/** The most specific line launchctl printed about a failure. */
function firstLine(err: unknown): string {
  const e = err as { stderr?: unknown } | null;
  const stderr = text(e?.stderr).trim();
  const raw = stderr !== "" ? stderr : err instanceof Error ? err.message : text(err);
  return raw.split("\n")[0] ?? "";
}

/**
 * True iff `label` is a whole entry in `launchctl list` output, whose lines are
 * `PID<TAB>Status<TAB>Label`. Substring matching would accept
 * `com.alice.kistrader.monitor.backup` as `…monitor`.
 */
function isListed(output: string, label: string): boolean {
  for (const line of output.split("\n")) {
    const fields = line.trim().split(/\s+/);
    if (fields[fields.length - 1] === label) return true;
  }
  return false;
}

/** Blocking pause. `Atomics.wait` sleeps the thread instead of burning it. */
function pause(ms: number): void {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

/** `launchctl list` output, or `null` when launchctl could not be reached. */
function listOutput(run: LaunchctlRunner): string | null {
  try {
    return run(["list"]);
  } catch {
    return null;
  }
}

export interface InstallResult {
  label: string;
  path: string;
  /** Observed in `launchctl list` — never inferred from an exit code. */
  loaded: boolean;
  message: string;
}

/**
 * Write the job's plist and load it, then confirm by observation.
 *
 * Never throws for a launchd failure: the caller (`kis-trader init`) installs
 * several jobs in a row and reports each one's outcome, so a single job that
 * will not load must not abort the rest.
 */
export function installJob(
  job: JobKey,
  cfg: Config,
  home: string,
  run: LaunchctlRunner = defaultRun,
): InstallResult {
  const label = labelFor(job);
  const path = plistPath(label);

  mkdirSync(dirname(path), { recursive: true });
  // The job cannot create its own log directory: launchd opens the log files
  // before the process starts, and a missing directory makes the job fail to
  // spawn with no diagnostic anywhere.
  mkdirSync(join(home, "logs"), { recursive: true });

  writeFileSync(path, renderPlist(job, cfg, home), { mode: 0o644 });
  // writeFileSync's mode only applies at creation; chmod covers an overwrite of
  // a plist that already existed with looser permissions.
  chmodSync(path, 0o644);

  const domain = guiDomain();

  // Not being loaded yet is the normal case on a first install, so a failing
  // bootout is not an error — but it does tell us whether the service slot was
  // occupied a moment ago, which is exactly when bootstrap needs retrying.
  let bootedOut = false;
  try {
    run(["bootout", `${domain}/${label}`]);
    bootedOut = true;
  } catch {
    bootedOut = false;
  }

  const attempts = bootedOut ? BOOTSTRAP_ATTEMPTS : 1;
  let bootstrapError = "";
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (attempt > 0) pause(BOOTSTRAP_PAUSE_MS);
    try {
      run(["bootstrap", domain, path]);
      bootstrapError = "";
      break;
    } catch (err) {
      bootstrapError = firstLine(err);
    }
  }

  const listed = listOutput(run);
  const loaded = listed !== null && isListed(listed, label);

  let message: string;
  if (loaded) {
    message = `${label} is loaded`;
  } else if (bootstrapError !== "") {
    message = `launchctl bootstrap failed for ${label}: ${bootstrapError}`;
  } else if (listed === null) {
    message = `${label} was bootstrapped but \`launchctl list\` could not be read`;
  } else {
    message = `${label} was bootstrapped but does not appear in \`launchctl list\``;
  }

  return { label, path, loaded, message };
}

export interface UninstallResult {
  label: string;
  path: string;
  /** Whether a plist file was actually deleted. */
  removed: boolean;
  message: string;
}

/**
 * Unload the job and delete its plist.
 *
 * The plist is removed even when the bootout fails: a job that is not loaded
 * boots out with "No such process", and leaving the file behind would silently
 * re-load it at the next login.
 */
export function uninstallJob(
  job: JobKey,
  run: LaunchctlRunner = defaultRun,
): UninstallResult {
  const label = labelFor(job);
  const path = plistPath(label);

  try {
    run(["bootout", `${guiDomain()}/${label}`]);
  } catch {
    // Not loaded, or launchd refused — either way the file still has to go.
  }

  if (!existsSync(path)) {
    return { label, path, removed: false, message: `no plist at ${path}` };
  }
  unlinkSync(path);
  return { label, path, removed: true, message: `removed ${path}` };
}

export type JobStatus = "loaded" | "installed-not-loaded" | "absent";

/**
 * What the machine currently has for this job.
 *
 * `absent` is decided by the file alone, so no launchctl call is made for a job
 * that was never installed.
 */
export function jobStatus(
  job: JobKey,
  run: LaunchctlRunner = defaultRun,
): JobStatus {
  const label = labelFor(job);
  if (!existsSync(plistPath(label))) return "absent";
  const listed = listOutput(run);
  return listed !== null && isListed(listed, label) ? "loaded" : "installed-not-loaded";
}
