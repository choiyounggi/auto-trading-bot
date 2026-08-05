#!/usr/bin/env node
/**
 * `kis-trader` — the CLI entry point.
 *
 * This module owns argument dispatch and the help text and nothing else: every
 * command delegates to a module that already exists (`setup`, `doctor`,
 * `launchd`, `bootstrap`, `config`). Adding behaviour here that belongs in one
 * of those is how the two drift apart.
 *
 * `parseArgv`, `COMMANDS` and `usage` are exported and pure so the tests can
 * cover dispatch without executing `main()` — which spawns launchctl, npm and
 * the project's Python interpreter on the caller's machine.
 */

import { spawnSync } from "node:child_process";
import { existsSync, realpathSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { printBanner, readPackageVersion } from "./banner.js";
import { venvPython } from "./bootstrap.js";
import { configHome, JOB_KEYS, loadConfig, type Config, type JobName } from "./config.js";
import { exitCodeFor, formatChecks, runDoctor } from "./doctor.js";
import { installJob, jobStatus, uninstallJob, JOBS, type JobKey } from "./launchd.js";
import { runInit } from "./setup.js";

/** The published name — `upgrade` reinstalls exactly this package. */
const PACKAGE_NAME = "@younggichoi/kis-trader";

/**
 * The package root, one level above the compiled entry point
 * (`<root>/dist/index.js`). This is both where `package.json` lives and the
 * directory the Python engine ships in, so `init` records it as `projectDir`.
 */
const PKG_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

export interface CommandSpec {
  name: string;
  summary: string;
  /**
   * Whether the banner precedes the command's output. `start` and `logs` are
   * live streams — the art would push their first lines off screen.
   */
  interactive: boolean;
}

/** The dispatch table. `usage()` is built from it, so help cannot go stale. */
export const COMMANDS: readonly CommandSpec[] = [
  { name: "init", summary: "Interactive setup: credentials, paths, launchd jobs", interactive: true },
  { name: "doctor", summary: "Diagnose the install (--json for machine output)", interactive: true },
  { name: "start", summary: "Run one job in the foreground: start <job>", interactive: false },
  { name: "logs", summary: "Tail a job's log: logs [job] [--err]", interactive: false },
  { name: "install-jobs", summary: "Install and load the launchd jobs enabled in config", interactive: true },
  { name: "uninstall-jobs", summary: "Unload and remove those launchd jobs", interactive: true },
  { name: "status", summary: "Show the launchd status of every job", interactive: true },
  { name: "upgrade", summary: "Reinstall the latest release, then refresh the jobs", interactive: true },
  { name: "help", summary: "Show this help", interactive: true },
];

/**
 * Split `process.argv.slice(2)` into a command and its arguments.
 *
 * No arguments, a blank first argument, `--help` and `-h` all resolve to
 * `help`, so `main()` has a single help branch and the empty case can never
 * fall through as a command named `""`.
 */
export function parseArgv(argv: string[]): { cmd: string; rest: string[] } {
  const [first, ...rest] = argv;
  if (first === undefined || first.trim() === "") return { cmd: "help", rest };
  if (first === "--help" || first === "-h") return { cmd: "help", rest };
  return { cmd: first, rest };
}

/** The help text, generated from `COMMANDS`. */
export function usage(version: string): string {
  const width = Math.max(0, ...COMMANDS.map((c) => c.name.length));
  const lines = [
    `kis-trader ${version === "" ? "(unknown version)" : `v${version}`}`,
    "",
    "Usage:",
    "  kis-trader <command> [options]",
    "",
    "Commands:",
    ...COMMANDS.map((c) => `  ${c.name.padEnd(width)}  ${c.summary}`),
    "",
    "Env:",
    "  KIS_TRADER_HOME  State directory holding config.json and logs/.",
    "                   Must be absolute. Default: ~/.kis-trader",
    "  KIS_MODE         paper | real. Set from config for the Python engine by",
    "                   the launchd jobs and by `doctor`; not read by this CLI.",
    "",
  ];
  return lines.join("\n") + "\n";
}

// ── helpers ───────────────────────────────────────────────────────────

const out = process.stdout;
const err = process.stderr;

function jobKeyOf(name: string): JobKey | null {
  return (JOB_KEYS as readonly string[]).includes(name) ? (name as JobName) : null;
}

function jobNames(): string {
  return JOB_KEYS.join(", ");
}

/** The first non-flag argument, or `null` when there is none. */
function positional(rest: string[]): string | null {
  return rest.find((a) => !a.startsWith("-")) ?? null;
}

/**
 * The config, or `null` after reporting why it could not be read. Every
 * machine-touching command needs it, and none of them can do anything useful
 * without it.
 */
function requireConfig(): Config | null {
  const parsed = loadConfig();
  if (parsed.ok) return parsed.value;
  err.write(`config unusable: ${parsed.errors.join("; ")}\n`);
  err.write("Run `kis-trader init` to create it.\n");
  return null;
}

// ── commands ──────────────────────────────────────────────────────────

function cmdDoctor(rest: string[]): number {
  const checks = runDoctor();
  out.write(
    (rest.includes("--json") ? JSON.stringify(checks, null, 2) : formatChecks(checks)) + "\n",
  );
  return exitCodeFor(checks);
}

function cmdStart(rest: string[]): number {
  const name = positional(rest);
  const job = name === null ? null : jobKeyOf(name);
  if (job === null) {
    err.write(
      name === null
        ? `start needs a job name. One of: ${jobNames()}\n`
        : `Unknown job: ${name}. One of: ${jobNames()}\n`,
    );
    return 2;
  }
  const cfg = requireConfig();
  if (cfg === null) return 1;

  // spawnSync, not spawn: `stdio: "inherit"` streams identically either way,
  // and the synchronous form is what lets the child's exit code become ours.
  const child = spawnSync(venvPython(cfg.projectDir), JOBS[job].args, {
    stdio: "inherit",
    cwd: cfg.projectDir,
  });
  if (child.error !== undefined) {
    err.write(`could not run ${venvPython(cfg.projectDir)}: ${child.error.message}\n`);
    return 1;
  }
  return child.status ?? 1;
}

function cmdLogs(rest: string[]): number {
  const name = positional(rest) ?? "orchestrator";
  const job = jobKeyOf(name);
  if (job === null) {
    err.write(`Unknown job: ${name}. One of: ${jobNames()}\n`);
    return 2;
  }
  // The stderr sibling is named the way launchd's StandardErrorPath is —
  // `<job>.err.log` — so `--err` names a file that actually exists.
  const base = JOBS[job].log;
  const file = rest.includes("--err") ? base.replace(/\.log$/, "") + ".err.log" : base;
  const path = join(configHome(), "logs", file);

  if (!existsSync(path)) {
    err.write(`no log file at ${path}\n`);
    err.write("The job writes it on its first run.\n");
    return 1;
  }
  const child = spawnSync("tail", ["-F", path], { stdio: "inherit" });
  if (child.error !== undefined) {
    err.write(`could not run tail: ${child.error.message}\n`);
    return 1;
  }
  return child.status ?? 0;
}

function cmdInstallJobs(): number {
  const cfg = requireConfig();
  if (cfg === null) return 1;
  const home = configHome();

  let failed = 0;
  for (const job of JOB_KEYS) {
    if (!cfg.jobs[job]) {
      out.write(`  ${job}: disabled in config, skipped\n`);
      continue;
    }
    const result = installJob(job, cfg, home);
    out.write(`${result.loaded ? "✔" : "✘"} ${job}: ${result.message}\n`);
    if (!result.loaded) failed += 1;
  }
  return failed === 0 ? 0 : 1;
}

function cmdUninstallJobs(): number {
  const cfg = requireConfig();
  if (cfg === null) return 1;

  for (const job of JOB_KEYS) {
    if (!cfg.jobs[job]) continue;
    const result = uninstallJob(job);
    out.write(`${result.removed ? "✔" : "·"} ${job}: ${result.message}\n`);
  }
  return 0;
}

function cmdStatus(): number {
  const width = Math.max(...JOB_KEYS.map((j) => j.length));
  for (const job of JOB_KEYS) {
    out.write(`  ${job.padEnd(width)}  ${jobStatus(job)}\n`);
  }
  return 0;
}

function cmdUpgrade(): number {
  const child = spawnSync("npm", ["install", "-g", `${PACKAGE_NAME}@latest`], {
    stdio: "inherit",
  });
  if (child.error !== undefined) {
    err.write(`could not run npm: ${child.error.message}\n`);
    return 1;
  }
  if (child.status !== 0) return child.status ?? 1;

  // The plists point at the old package path, so they have to be rewritten
  // before the upgraded engine is what launchd actually runs.
  out.write("\nRefreshing launchd jobs so they point at the new install…\n");
  return cmdInstallJobs();
}

// ── dispatch ──────────────────────────────────────────────────────────

export async function main(argv: string[]): Promise<number> {
  const { cmd, rest } = parseArgv(argv);
  const version = readPackageVersion(PKG_ROOT);
  const spec = COMMANDS.find((c) => c.name === cmd);

  // `doctor --json` is parsed by other programs; a banner on stdout would make
  // it invalid JSON, so machine output suppresses the decoration.
  const machineOutput = cmd === "doctor" && rest.includes("--json");
  if (spec !== undefined && spec.interactive && !machineOutput) printBanner(version);

  switch (cmd) {
    case "init":
      return await runInit({ home: configHome(), projectDir: PKG_ROOT });
    case "doctor":
      return cmdDoctor(rest);
    case "start":
      return cmdStart(rest);
    case "logs":
      return cmdLogs(rest);
    case "install-jobs":
      return cmdInstallJobs();
    case "uninstall-jobs":
      return cmdUninstallJobs();
    case "status":
      return cmdStatus();
    case "upgrade":
      return cmdUpgrade();
    case "help":
      out.write(usage(version));
      return 0;
    default:
      err.write(`Unknown command: ${cmd}\n\n`);
      err.write(usage(version));
      return 2;
  }
}

/**
 * True only when this file is the process entry point.
 *
 * Importing the module — which the tests do — must not dispatch anything. The
 * paths are resolved through `realpath` because npm installs the `bin` as a
 * symlink, so `process.argv[1]` is the link, not this file.
 */
function isEntryPoint(): boolean {
  const invoked = process.argv[1];
  if (invoked === undefined) return false;
  try {
    return realpathSync(invoked) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
}

if (isEntryPoint()) {
  main(process.argv.slice(2))
    .then((code) => {
      // Not process.exit(): that would truncate output still queued on a pipe.
      process.exitCode = code;
    })
    .catch((error: unknown) => {
      err.write(
        (error instanceof Error ? (error.stack ?? error.message) : String(error)) + "\n",
      );
      process.exitCode = 1;
    });
}
