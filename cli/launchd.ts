/**
 * launchd LaunchAgents for the nine trading jobs: render, install, uninstall,
 * status.
 *
 * The plists are rendered at runtime from the user's config rather than shipped
 * as files, because every path in them belongs to the machine the CLI is
 * installed on — a committed plist carries the author's home directory and is
 * wrong everywhere else.
 *
 * A launchd job gets a minimal environment and loads no rc files, so nothing
 * here may rely on the user's shell: the interpreter is `venvPython(stateDir)`,
 * an absolute path a version-manager shim cannot shadow, and every variable the
 * engine needs (`PATH`, `HOME`, `KIS_TRADER_HOME`, `KIS_MODE`,
 * `KIS_TRADER_SIGNAL_DIR`) is declared inside the plist. Both jobs' streams get
 * an explicit log file — a service without a log sink fails silently.
 *
 * The interpreter is the **venv's**, not the bare `cfg.pythonPath`. Measured:
 * the configured interpreter has no pydantic, sqlalchemy or pandas, so a job
 * pointed at it dies with an ImportError on its first scheduled run — into a
 * log nobody is watching. `cfg.pythonPath` still builds the venv and still
 * seeds `PATH`; it just never runs a job.
 *
 * Installation is confirmed by *observation*: `launchctl bootstrap` exiting 0
 * means the request was accepted, not that the job is loaded, so `installJob`
 * only reports `loaded: true` when the label actually shows up in
 * `launchctl list`.
 *
 * Only `telegramAgent` carries `KeepAlive`: it is a long-lived daemon, and a
 * supervisor restart is the supported way to recover one. Every other job is a
 * one-shot run on a schedule, where KeepAlive would make launchd restart it the
 * moment it finished.
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

import { venvPython } from "./bootstrap.js";
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

/**
 * A guarded job's lock is reclaimed once it is this old.
 *
 * Sized against the work, not the schedule: a 260-day-lookback signal run is
 * minutes, so 15 covers a slow run with room to spare, while still letting the
 * *next* day's schedule proceed after a crash rather than skipping forever.
 */
const STALE_LOCK_MINUTES = 15;

/** PATH entries appended after the interpreter's own directory. */
const BASE_PATH = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"];

/**
 * Seconds launchd waits before restarting a `keepAlive` job.
 *
 * Copied from the hand-installed telegram agent plist this job adopts. It is
 * also launchd's own default, but stating it keeps a crash-looping daemon's
 * backoff visible in the file rather than implied.
 */
const THROTTLE_SECONDS = 30;

export interface JobSpec {
  /** Arguments after the interpreter — always a `-m module` invocation. */
  args: string[];
  schedule:
    | { hour: number; minute: number }
    /** Several fixed times on each weekday — `signalUs` runs at 22:35 and 23:35. */
    | { times: { hour: number; minute: number }[] }
    | { intervalSec: number }
    /**
     * A long-lived daemon: started at load and restarted by launchd whenever it
     * exits. Carries no schedule at all — a scheduled start on top of KeepAlive
     * would have launchd run a second copy alongside the one already up.
     */
    | { keepAlive: true };
  /** Basename of the stdout log; stderr gets the `.err.log` sibling. */
  log: string;
  /**
   * Wrap the run in the overlap/hang guard (see `guardScript`).
   *
   * Only the signal jobs need it: they can run for minutes over a 260-day
   * lookback, so the 16:30 run may still be going when the next schedule fires,
   * and a second concurrent pykrx session against the same account is not
   * something to find out about in production. The trading jobs are short and
   * already idempotent.
   */
  guarded?: boolean;
}

/**
 * Reallocation check times — weekdays 09:30 through 14:30, every 30 minutes,
 * 11 times.
 *
 * After the 09:05 entry job has finished, up to one slot before the regular
 * session closes (15:30). Paper fills only happen during the regular session,
 * so no time after it is scheduled.
 *
 * 15:00 is deliberately absent: `dipBuy` already fires then, and both jobs
 * spend from the same cash. Each is independently capped by the broker's
 * buying power, so the collision costs a rejected order rather than
 * over-exposure — but a rejection is noise, and the 15:00 slot is the one
 * with the least to lose. Its orders would have 30 minutes to fill, and the
 * 14:30 slot already covers late-session redeployment. `dipBuy`'s 15:00 is
 * fixed by its own design (paper fills need the regular session), so this is
 * the side that moves.
 */
const CASH_DEPLOY_TIMES = Array.from({ length: 11 }, (_, i) => {
  const minutes = 9 * 60 + 30 + i * 30;
  return { hour: Math.floor(minutes / 60), minute: minutes % 60 };
});

/**
 * The nine jobs, with the schedules the previously hand-installed plists ran on.
 * Keyed by `JobName`, so a job added to `config.ts` without a schedule here is a
 * type error rather than a job that silently never installs.
 *
 * Ordering matters between the two halves: the signal producer must finish
 * before the trader reads its output. KR is 16:30 → 16:45, US is 22:35 → 22:45.
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
  // Intraday cash reallocation — reinvests cash freed by an exit the same day.
  // 30-minute cadence means the previous run can still be going, so this is
  // guarded: an overlapping run exits 0 immediately. The Python side caps
  // candidates per run at 4 (cash_deploy.max_candidates_per_run), bounding a
  // single run to at most 4 x 180s = 12 minutes, comfortably inside
  // STALE_LOCK_MINUTES (15).
  cashDeploy: {
    args: ["-m", "src.orchestrator", "--deploy-cash"],
    schedule: { times: CASH_DEPLOY_TIMES },
    log: "cashDeploy.log",
    guarded: true,
  },
  usOrchestrator: {
    args: ["-m", "src.orchestrator", "--asset-class", "overseas_stock"],
    schedule: { hour: 22, minute: 45 },
    log: "usOrchestrator.log",
  },
  // The two signal jobs' arguments are copied from the shell scripts that ran
  // them before the port (`run_daily.sh`, `run_us_open.sh`), including the
  // 260-day lookback the turtle_breakout strategy needs for its 200-day line.
  signalKr: {
    args: ["-m", "src.signal.main", "--lookback", "260"],
    schedule: { hour: 16, minute: 30 },
    log: "signalKr.log",
    guarded: true,
  },
  signalUs: {
    args: ["-m", "src.signal.main", "--overseas-only", "--lookback", "260", "--no-llm"],
    schedule: { times: [{ hour: 22, minute: 35 }, { hour: 23, minute: 35 }] },
    log: "signalUs.log",
    guarded: true,
  },
  // Adopted from a plist installed by hand: the same module, `RunAtLoad`,
  // `KeepAlive` and a 30s throttle. Deliberately **not** guarded — the guard
  // skips a run whose predecessor still holds the lock, and a daemon that never
  // exits holds it forever, so every restart after the first would exit 0 as if
  // it had been skipped and the agent would never come back up.
  telegramAgent: {
    args: ["-m", "src.agent.telegram_agent"],
    schedule: { keepAlive: true },
    log: "telegramAgent.log",
  },
};

/** Where a guarded job's lock directory lives. */
export function lockPath(job: JobKey, home: string): string {
  return join(home, "locks", `${job}.lock`);
}

/**
 * The `sh -c` program for a guarded job.
 *
 * `mkdir` is the lock because it is atomic on POSIX: the second concurrent run
 * fails to create the directory and exits 0, which is skip-on-overlap. That
 * alone is a trap — a run that dies without releasing leaves the lock forever
 * and every later schedule silently skips — so a lock older than
 * `STALE_LOCK_MINUTES` is reclaimed. `trap … EXIT` covers the normal path.
 *
 * The command is **not** `exec`'d. `exec` replaces the shell, which discards the
 * EXIT trap, so the lock would survive every successful run and each job would
 * only start once the staleness window had passed. Measured: with `exec`, a
 * completed run left its lock directory behind. Running the command as a child
 * keeps the shell alive to release it, and `$?` is still the job's exit status.
 *
 * Deliberately built from stock tools only. `flock` and `timeout` are the
 * obvious choices and **neither ships with macOS** (measured: `/usr/bin/flock`
 * and `/usr/bin/timeout` are absent; the Homebrew `timeout` exists only where
 * coreutils is installed, which this package cannot assume). `mkdir` and BSD
 * `find -mmin` are always there.
 */
export function guardScript(job: JobKey, cfg: Config, home: string): string {
  const lock = lockPath(job, home);
  const cmd = [venvPython(cfg.stateDir), ...JOBS[job].args].map(shq).join(" ");
  return (
    `L=${shq(lock)}; ` +
    `if ! mkdir "$L" 2>/dev/null; then ` +
    `if [ -n "$(find "$L" -maxdepth 0 -mmin +${STALE_LOCK_MINUTES} 2>/dev/null)" ]; then ` +
    `rmdir "$L" 2>/dev/null; mkdir "$L" 2>/dev/null || exit 0; ` +
    `else exit 0; fi; fi; ` +
    `trap 'rmdir "$L" 2>/dev/null' EXIT; ` +
    cmd
  );
}

/** Single-quote for `sh`, closing and reopening around any embedded quote. */
function shq(value: string): string {
  return `'${value.replaceAll("'", `'\\''`)}'`;
}

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
  // A supervised daemon: launchd starts it at load and restarts it on exit, so
  // it gets no StartCalendarInterval and no StartInterval at all.
  if ("keepAlive" in schedule) {
    return (
      "  <key>RunAtLoad</key>\n  <true/>\n" +
      "  <key>KeepAlive</key>\n  <true/>\n" +
      `  <key>ThrottleInterval</key>\n  <integer>${THROTTLE_SECONDS}</integer>\n`
    );
  }
  if ("intervalSec" in schedule) {
    return `  <key>StartInterval</key>\n  <integer>${schedule.intervalSec}</integer>\n`;
  }
  // One entry per time, so the single-time form renders exactly as before.
  const times = "times" in schedule ? schedule.times : [schedule];
  // Weekday 1–5 is Monday–Friday: the markets these jobs trade are closed on
  // weekends, and launchd has no "weekdays" shorthand. A job with two times
  // therefore emits 5 × 2 dicts.
  const days = [1, 2, 3, 4, 5]
    .flatMap((day) =>
      times.map(
        (t) =>
          `    <dict><key>Weekday</key><integer>${day}</integer>` +
          `<key>Hour</key><integer>${t.hour}</integer>` +
          `<key>Minute</key><integer>${t.minute}</integer></dict>\n`,
      ),
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
  // The venv interpreter, not `cfg.pythonPath`: the bare one has none of the
  // engine's dependencies (measured — no pydantic, sqlalchemy or pandas).
  const argv = JOBS[job].guarded
    ? ["/bin/sh", "-c", guardScript(job, cfg, home)]
    : [venvPython(cfg.stateDir), ...JOBS[job].args];
  const program = argv
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
  // The guarded jobs create their lock *inside* this directory, and `mkdir`
  // fails rather than creating parents — so the parent has to exist before the
  // first run, or every guarded run would exit 0 as if it had been skipped.
  mkdirSync(join(home, "locks"), { recursive: true });

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
