/**
 * Bootstrap the Python side of the trading engine: virtualenv, dependencies,
 * SQLite schema. This is `scripts/setup.sh` ported to TypeScript so the CLI can
 * report progress step by step instead of streaming a shell script's output.
 *
 * Every external call goes through an injectable `RunFn`. That is what makes
 * the module testable without building a real venv, and it is also where the
 * non-interactive rules live:
 *
 *   - fd 0 is detached by default (`stdio[0] = "ignore"`). `pip` and `venv` can
 *     prompt, and a prompt on an inherited terminal hangs forever; with stdin
 *     closed it fails fast instead. The one step that genuinely needs input —
 *     sqlite3 reading a migration — gets a *file* on fd 0, never a terminal.
 *   - every call is bounded by a timeout, so a wedged download fails the step
 *     rather than the whole install.
 *   - stdout and stderr are captured together into the step's `detail`, so an
 *     empty result is recorded evidence rather than a blank terminal nobody
 *     kept.
 *
 * The interpreter is always the absolute path from the config. A bare `python3`
 * resolves through rc files this process never loads, and on this machine it is
 * a version the engine does not support.
 */

import { spawnSync } from "node:child_process";
import { closeSync, existsSync, mkdirSync, openSync, readdirSync } from "node:fs";
import { join } from "node:path";

import type { Config } from "./config.js";
import { isExecutableFile } from "./resolve-cli-path.js";

/** One bootstrap step's outcome. `detail` is what the CLI prints beside it. */
export type StepResult = { step: string; ok: boolean; detail: string };

export interface RunOptions {
  /** Working directory for the child. */
  cwd?: string;
  /** File to attach to fd 0. Absent means fd 0 is detached entirely. */
  stdinFile?: string;
}

/** Runs one external command to completion. `code` is `-1` when it never ran. */
export type RunFn = (
  cmd: string,
  args: string[],
  timeoutMs: number,
  opts?: RunOptions,
) => { code: number; out: string };

/**
 * Apple's sqlite3, by absolute path. It ships with macOS, so there is nothing
 * to install; the absolute path keeps a shadowed `sqlite3` on PATH out of the
 * schema.
 */
export const SQLITE3 = "/usr/bin/sqlite3";

/** Per-step upper bounds. Creating a venv is fast; a cold wheel build is not. */
const VENV_TIMEOUT_MS = 180_000;
const PIP_UPGRADE_TIMEOUT_MS = 300_000;
const DEPS_TIMEOUT_MS = 900_000;
const MIGRATION_TIMEOUT_MS = 60_000;

/** Failure output longer than this is truncated — a `detail` is a line, not a log. */
const DETAIL_MAX_LEN = 400;

/**
 * sqlite3 fragments meaning "this migration ran before". Migrations are not
 * tracked in a table, so re-application is the normal path, and re-adding a
 * column or table is the shape that failure takes.
 */
const ALREADY_APPLIED_MARKERS = ["already exists", "duplicate column"];

/** Absolute path of the interpreter inside the project's virtualenv. */
export function venvPython(projectDir: string): string {
  return join(projectDir, ".venv", "bin", "python");
}

/**
 * The real runner.
 *
 * Never throws: a missing binary, an unreadable stdin file and a timeout all
 * come back as `code: -1` with the reason in `out`, so a caller reading step
 * results never has to also guard against exceptions.
 */
export const defaultRun: RunFn = (cmd, args, timeoutMs, opts = {}) => {
  let stdin: number | "ignore" = "ignore";
  if (opts.stdinFile !== undefined) {
    try {
      stdin = openSync(opts.stdinFile, "r");
    } catch (err) {
      return { code: -1, out: String(err) };
    }
  }
  try {
    const res = spawnSync(cmd, args, {
      cwd: opts.cwd,
      timeout: timeoutMs,
      encoding: "utf8",
      stdio: [stdin, "pipe", "pipe"],
    });
    let out = `${res.stdout ?? ""}${res.stderr ?? ""}`;
    if (res.error) {
      // ENOENT, ETIMEDOUT (the timeout kill) — the message is the only evidence.
      out += (out === "" ? "" : "\n") + res.error.message;
      return { code: -1, out };
    }
    return { code: res.status ?? -1, out };
  } finally {
    if (typeof stdin === "number") closeSync(stdin);
  }
};

export interface BootstrapOptions {
  /** Override the command runner (testing only). */
  run?: RunFn;
  /** Called once per step, as soon as that step finishes. */
  onStep?: (r: StepResult) => void;
}

/** Collapse command output into a single-line `detail`, bounded in length. */
function clip(out: string): string {
  const flat = out.trim().replace(/\s+/g, " ");
  return flat.length > DETAIL_MAX_LEN ? `${flat.slice(0, DETAIL_MAX_LEN)}…` : flat;
}

function failure(code: number, out: string): string {
  const body = clip(out);
  return body === "" ? `exit ${code}` : `exit ${code}: ${body}`;
}

function isAlreadyApplied(out: string): boolean {
  const lower = out.toLowerCase();
  return ALREADY_APPLIED_MARKERS.some((m) => lower.includes(m));
}

/**
 * Create the venv, install the project, and apply every migration.
 *
 * Steps run in order and stop at the first failure — installing dependencies
 * into a venv that was never created, or migrating with a half-installed
 * project, only produces a second, more confusing error. The returned array is
 * therefore shorter than four whenever something failed, and its last entry is
 * the failure.
 */
export function bootstrapPython(cfg: Config, opts: BootstrapOptions = {}): StepResult[] {
  const run = opts.run ?? defaultRun;
  const results: StepResult[] = [];

  /** Record a step, notify the caller, and report whether to continue. */
  const emit = (step: string, ok: boolean, detail: string): boolean => {
    const result: StepResult = { step, ok, detail };
    results.push(result);
    opts.onStep?.(result);
    return ok;
  };

  const py = venvPython(cfg.projectDir);

  // 1. venv — an existing interpreter is reused. Rebuilding one would discard
  //    an already-installed dependency set for no gain.
  if (isExecutableFile(py)) {
    if (!emit("venv", true, "already exists")) return results;
  } else {
    const r = run(cfg.pythonPath, ["-m", "venv", join(cfg.projectDir, ".venv")], VENV_TIMEOUT_MS);
    if (!emit("venv", r.code === 0, r.code === 0 ? "created" : failure(r.code, r.out))) {
      return results;
    }
  }

  // 2. pip-upgrade — the interpreter's bundled pip is often too old to read a
  //    modern wheel's metadata, which surfaces as a baffling failure in step 3.
  {
    const r = run(py, ["-m", "pip", "install", "--upgrade", "pip", "-q"], PIP_UPGRADE_TIMEOUT_MS);
    if (!emit("pip-upgrade", r.code === 0, r.code === 0 ? "upgraded" : failure(r.code, r.out))) {
      return results;
    }
  }

  // 3. deps — `.[dev]` is relative to the project, so this one runs from there.
  {
    const r = run(py, ["-m", "pip", "install", "-e", ".[dev]", "-q"], DEPS_TIMEOUT_MS, {
      cwd: cfg.projectDir,
    });
    if (!emit("deps", r.code === 0, r.code === 0 ? "installed" : failure(r.code, r.out))) {
      return results;
    }
  }

  // 4. migrations
  const dataDir = join(cfg.projectDir, "data");
  try {
    mkdirSync(dataDir, { recursive: true });
  } catch (err) {
    emit("migrations", false, `cannot create ${dataDir}: ${String(err)}`);
    return results;
  }

  const migrationsDir = join(dataDir, "migrations");
  let files: string[];
  try {
    files = readdirSync(migrationsDir, { withFileTypes: true })
      .filter((e) => e.isFile() && e.name.endsWith(".sql"))
      .map((e) => e.name)
      .sort();
  } catch {
    // Not "nothing to do": a project without its migrations is not the project,
    // and passing here would leave a schema-less database looking bootstrapped.
    emit("migrations", false, `no migrations directory at ${migrationsDir}`);
    return results;
  }

  const db = join(dataDir, "trades.sqlite");
  let applied = 0;
  let skipped = 0;
  for (const name of files) {
    // The SQL arrives on fd 0 from a file — the one place a readable stdin is
    // correct, and still never a terminal.
    const r = run(SQLITE3, [db], MIGRATION_TIMEOUT_MS, {
      stdinFile: join(migrationsDir, name),
    });
    if (r.code === 0) {
      applied += 1;
      continue;
    }
    if (isAlreadyApplied(r.out)) {
      skipped += 1;
      continue;
    }
    emit("migrations", false, `${name}: ${failure(r.code, r.out)}`);
    return results;
  }

  let detail: string;
  if (files.length === 0) detail = "no migrations";
  else if (applied === 0) detail = "already applied";
  else if (skipped === 0) detail = `${applied} applied`;
  else detail = `${applied} applied, ${skipped} already applied`;
  emit("migrations", true, detail);

  return results;
}

/**
 * True when the project has both halves of a bootstrap: a usable venv
 * interpreter and the database. Either alone means an interrupted setup, which
 * the caller should finish rather than skip.
 */
export function isBootstrapped(cfg: Config): boolean {
  return (
    isExecutableFile(venvPython(cfg.projectDir)) &&
    existsSync(join(cfg.projectDir, "data", "trades.sqlite"))
  );
}
