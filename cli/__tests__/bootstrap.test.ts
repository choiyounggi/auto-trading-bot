import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  venvPython,
  bootstrapPython,
  isBootstrapped,
  defaultRun,
  SQLITE3,
  type RunFn,
  type RunOptions,
  type StepResult,
} from "../bootstrap.js";
import type { Config } from "../config.js";

/**
 * Scratch space lives inside the repo (`dist-test/.tmp`, already gitignored)
 * rather than the system temp dir: some fixtures carry the executable bit, and
 * writing +x files under /tmp is what endpoint security flags.
 *
 * No test here creates a real venv, installs a package, or runs sqlite3: every
 * `bootstrapPython` case injects `run`. The handful of `defaultRun` tests do
 * spawn a process, but only `/bin/cat`, `/bin/pwd` and `/bin/sh -c echo` —
 * they prove the fd-0 / timeout / output-capture rules and touch nothing.
 */
const SCRATCH_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", ".tmp");

function tmp(): string {
  mkdirSync(SCRATCH_ROOT, { recursive: true });
  return mkdtempSync(join(SCRATCH_ROOT, "bootstrap-"));
}

function withTmp(fn: (dir: string) => void): void {
  const dir = tmp();
  try {
    fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** Create a file carrying an executable bit. Never executed — only stat'ed. */
function writeExe(path: string): string {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  return path;
}

function writeFile(path: string, body = "x\n"): string {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, body, { mode: 0o644 });
  return path;
}

const PYTHON = "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11";

function cfgFor(projectDir: string, pythonPath = PYTHON): Config {
  return {
    mode: "paper",
    projectDir,
    pythonPath,
    signalDir: join(projectDir, "data", "signals"),
    llmAgent: "claude",
  newsLlmBackend: "none",
    jobs: {
      orchestrator: true,
      monitor: true,
      reconciler: true,
      dipBuy: true,
      usOrchestrator: true,
    signalKr: true,
    signalUs: true,
    },
  };
}

/**
 * Build a project fixture. `migrations` maps filename → SQL body; `null` means
 * the `data/migrations` directory is not created at all.
 */
function project(dir: string, migrations: Record<string, string> | null): Config {
  if (migrations !== null) {
    const migDir = join(dir, "data", "migrations");
    mkdirSync(migDir, { recursive: true });
    for (const [name, body] of Object.entries(migrations)) {
      writeFile(join(migDir, name), body);
    }
  }
  return cfgFor(dir);
}

interface Call {
  cmd: string;
  args: string[];
  timeoutMs: number;
  opts: RunOptions | undefined;
}

type Reply = { code: number; out: string };

/** A `RunFn` stub recording every call, answering exit 0 unless told otherwise. */
function runStub(
  reply: (call: Call, index: number) => Reply = () => ({ code: 0, out: "" }),
): RunFn & { calls: Call[] } {
  const calls: Call[] = [];
  const fn: RunFn = (cmd, args, timeoutMs, opts) => {
    calls.push({ cmd, args, timeoutMs, opts });
    return reply(calls[calls.length - 1], calls.length - 1);
  };
  return Object.assign(fn, { calls });
}

/** Fail the run whose command is `SQLITE3` and whose stdin file ends with `name`. */
function failMigration(name: string, out: string) {
  return (call: Call): Reply =>
    call.cmd === SQLITE3 && (call.opts?.stdinFile ?? "").endsWith(name)
      ? { code: 1, out }
      : { code: 0, out: "" };
}

const ONE_MIGRATION = { "0001_init.sql": "CREATE TABLE positions (a INT);\n" };

function steps(results: StepResult[]): string[] {
  return results.map((r) => r.step);
}

// ── venvPython ────────────────────────────────────────────────────────

test("venvPython points at .venv/bin/python under the project directory", () => {
  assert.equal(venvPython("/opt/bot"), "/opt/bot/.venv/bin/python");
});

test("venvPython keeps the result absolute and normalises a trailing slash", () => {
  assert.equal(venvPython("/opt/bot/"), "/opt/bot/.venv/bin/python");
  assert.ok(venvPython("/opt/bot").startsWith("/"));
});

// ── bootstrapPython: normal ───────────────────────────────────────────

test("all four steps succeed, in order, and onStep fires exactly four times", () => {
  withTmp((dir) => {
    const cfg = project(dir, ONE_MIGRATION);
    const run = runStub();
    const seen: StepResult[] = [];
    const results = bootstrapPython(cfg, { run, onStep: (r) => seen.push(r) });

    assert.deepEqual(steps(results), ["venv", "pip-upgrade", "deps", "migrations"]);
    for (const r of results) assert.equal(r.ok, true, `${r.step}: ${r.detail}`);
    assert.equal(seen.length, 4);
    assert.deepEqual(seen, results, "onStep receives each result as it is produced");
  });
});

test("the venv step invokes the configured absolute interpreter, never a bare name", () => {
  withTmp((dir) => {
    const cfg = project(dir, ONE_MIGRATION);
    const run = runStub();
    bootstrapPython(cfg, { run });

    const first = run.calls[0];
    assert.equal(first.cmd, PYTHON);
    assert.ok(first.cmd.startsWith("/"), "the interpreter must be an absolute path");
    assert.notEqual(first.cmd, "python3", "a bare name would resolve under launchd to nothing");
    assert.deepEqual(first.args, ["-m", "venv", join(dir, ".venv")]);
    assert.equal(first.timeoutMs, 180_000);
  });
});

test("pip-upgrade and deps run through the venv interpreter, deps from projectDir", () => {
  withTmp((dir) => {
    const cfg = project(dir, ONE_MIGRATION);
    const run = runStub();
    bootstrapPython(cfg, { run });

    const py = venvPython(dir);
    assert.equal(run.calls[1].cmd, py);
    assert.deepEqual(run.calls[1].args, ["-m", "pip", "install", "--upgrade", "pip", "-q"]);
    assert.equal(run.calls[1].timeoutMs, 300_000);

    assert.equal(run.calls[2].cmd, py);
    assert.deepEqual(run.calls[2].args, ["-m", "pip", "install", "-e", ".[dev]", "-q"]);
    assert.equal(run.calls[2].timeoutMs, 900_000);
    assert.equal(run.calls[2].opts?.cwd, dir, "`.[dev]` is only meaningful from projectDir");
  });
});

test("migrations feed each .sql file to /usr/bin/sqlite3 on stdin, sorted by name", () => {
  withTmp((dir) => {
    const cfg = project(dir, {
      "0010_late.sql": "-- late\n",
      "0002_second.sql": "-- second\n",
      "0001_init.sql": "-- init\n",
    });
    const run = runStub();
    bootstrapPython(cfg, { run });

    const db = join(dir, "data", "trades.sqlite");
    const sqliteCalls = run.calls.filter((c) => c.cmd === SQLITE3);
    assert.equal(sqliteCalls.length, 3);
    assert.deepEqual(
      sqliteCalls.map((c) => c.opts?.stdinFile),
      [
        join(dir, "data", "migrations", "0001_init.sql"),
        join(dir, "data", "migrations", "0002_second.sql"),
        join(dir, "data", "migrations", "0010_late.sql"),
      ],
    );
    for (const c of sqliteCalls) {
      assert.deepEqual(c.args, [db]);
      assert.ok(c.timeoutMs > 0, "every call is bounded by a timeout");
    }
  });
});

test("only the migrations step attaches stdin; venv/pip/deps leave fd 0 detached", () => {
  withTmp((dir) => {
    const cfg = project(dir, ONE_MIGRATION);
    const run = runStub();
    bootstrapPython(cfg, { run });

    for (const c of run.calls.slice(0, 3)) {
      assert.equal(c.opts?.stdinFile, undefined, `${c.cmd} must not inherit a readable fd 0`);
    }
    assert.ok(run.calls[3].opts?.stdinFile, "sqlite3 reads the migration from a file, not a terminal");
  });
});

test("bootstrapPython works with no onStep callback supplied", () => {
  withTmp((dir) => {
    const cfg = project(dir, ONE_MIGRATION);
    let results: StepResult[] = [];
    assert.doesNotThrow(() => {
      results = bootstrapPython(cfg, { run: runStub() });
    });
    assert.equal(results.length, 4);
  });
});

test("bootstrapPython creates data/ before applying migrations", () => {
  withTmp((dir) => {
    // Fixture writes data/migrations, so remove data/ entirely and re-add only
    // the migrations dir to prove the step does not depend on data/ existing.
    const cfg = cfgFor(dir);
    mkdirSync(join(dir, "data", "migrations"), { recursive: true });
    const run = runStub();
    const results = bootstrapPython(cfg, { run });
    assert.equal(results[3].ok, true);
  });
});

// ── bootstrapPython: error ────────────────────────────────────────────

test("a failing deps step short-circuits: three results, no migrations step", () => {
  withTmp((dir) => {
    const cfg = project(dir, ONE_MIGRATION);
    const run = runStub((c) =>
      c.args.includes(".[dev]")
        ? { code: 1, out: "ERROR: no matching distribution found for pandas" }
        : { code: 0, out: "" },
    );
    const seen: StepResult[] = [];
    const results = bootstrapPython(cfg, { run, onStep: (r) => seen.push(r) });

    assert.equal(results.length, 3, "the migrations step must never run");
    assert.deepEqual(steps(results), ["venv", "pip-upgrade", "deps"]);
    assert.equal(results[2].ok, false);
    assert.match(results[2].detail, /no matching distribution/);
    assert.equal(seen.length, 3);
    assert.equal(
      run.calls.some((c) => c.cmd === SQLITE3),
      false,
      "sqlite3 must not be invoked after a failed dependency install",
    );
  });
});

test("a failing venv step yields exactly one result", () => {
  withTmp((dir) => {
    const cfg = project(dir, ONE_MIGRATION);
    const run = runStub(() => ({ code: 1, out: "No module named venv" }));
    const results = bootstrapPython(cfg, { run });
    assert.equal(results.length, 1);
    assert.equal(results[0].step, "venv");
    assert.equal(results[0].ok, false);
    assert.match(results[0].detail, /No module named venv/);
    assert.equal(run.calls.length, 1, "no later step is attempted");
  });
});

test("a failing pip-upgrade step yields exactly two results", () => {
  withTmp((dir) => {
    const cfg = project(dir, ONE_MIGRATION);
    const run = runStub((c) =>
      c.args.includes("--upgrade") ? { code: 2, out: "SSL: CERTIFICATE_VERIFY_FAILED" } : { code: 0, out: "" },
    );
    const results = bootstrapPython(cfg, { run });
    assert.deepEqual(steps(results), ["venv", "pip-upgrade"]);
    assert.equal(results[1].ok, false);
    assert.match(results[1].detail, /CERTIFICATE_VERIFY_FAILED/);
  });
});

test("a migration failing for an unrelated reason fails the step", () => {
  withTmp((dir) => {
    const cfg = project(dir, ONE_MIGRATION);
    const run = runStub(failMigration("0001_init.sql", "Error: disk I/O error"));
    const results = bootstrapPython(cfg, { run });

    assert.equal(results.length, 4);
    assert.equal(results[3].step, "migrations");
    assert.equal(results[3].ok, false);
    assert.match(results[3].detail, /disk I\/O error/);
    assert.match(results[3].detail, /0001_init\.sql/, "the failing file is named");
  });
});

test("a failing migration stops the remaining migration files", () => {
  withTmp((dir) => {
    const cfg = project(dir, {
      "0001_init.sql": "-- a\n",
      "0002_next.sql": "-- b\n",
      "0003_last.sql": "-- c\n",
    });
    const run = runStub(failMigration("0002_next.sql", "Error: database is locked"));
    const results = bootstrapPython(cfg, { run });

    assert.equal(results[3].ok, false);
    const applied = run.calls
      .filter((c) => c.cmd === SQLITE3)
      .map((c) => c.opts?.stdinFile ?? "");
    assert.equal(applied.length, 2, "0003 must not be applied after 0002 failed");
    assert.ok(applied[1].endsWith("0002_next.sql"));
  });
});

test("a missing data/migrations directory fails the step rather than silently passing", () => {
  withTmp((dir) => {
    const cfg = project(dir, null);
    const run = runStub();
    const results = bootstrapPython(cfg, { run });

    assert.equal(results.length, 4);
    assert.equal(results[3].ok, false);
    assert.match(results[3].detail, /migrations/);
    assert.equal(
      run.calls.some((c) => c.cmd === SQLITE3),
      false,
    );
  });
});

test("an unusable projectDir fails the migrations step instead of throwing", () => {
  withTmp((dir) => {
    // projectDir is a regular file, so mkdir of <projectDir>/data cannot work.
    const asFile = writeFile(join(dir, "not-a-dir"));
    const cfg = cfgFor(asFile);
    let results: StepResult[] = [];
    assert.doesNotThrow(() => {
      results = bootstrapPython(cfg, { run: runStub() });
    });
    assert.equal(results.length, 4);
    assert.equal(results[3].ok, false);
    assert.notEqual(results[3].detail, "");
  });
});

// ── bootstrapPython: boundary ─────────────────────────────────────────

test("an already-applied migration ('already exists') is not a failure", () => {
  withTmp((dir) => {
    const cfg = project(dir, ONE_MIGRATION);
    const run = runStub(
      failMigration("0001_init.sql", "Parse error near line 1: table positions already exists"),
    );
    const results = bootstrapPython(cfg, { run });

    assert.equal(results[3].ok, true);
    assert.equal(results[3].detail, "already applied");
  });
});

test("an already-applied migration ('duplicate column') is not a failure", () => {
  withTmp((dir) => {
    const cfg = project(dir, ONE_MIGRATION);
    const run = runStub(
      failMigration("0001_init.sql", "Parse error near line 5: duplicate column name: ticker"),
    );
    const results = bootstrapPython(cfg, { run });

    assert.equal(results[3].ok, true);
    assert.equal(results[3].detail, "already applied");
  });
});

test("a mix of fresh and already-applied migrations reports both counts", () => {
  withTmp((dir) => {
    const cfg = project(dir, { "0001_init.sql": "-- a\n", "0002_next.sql": "-- b\n" });
    const run = runStub(
      failMigration("0002_next.sql", "Error: duplicate column name: qty_remaining"),
    );
    const results = bootstrapPython(cfg, { run });

    assert.equal(results[3].ok, true);
    assert.match(results[3].detail, /1 applied/);
    assert.match(results[3].detail, /1 already applied/);
  });
});

test("an existing .venv/bin/python skips the venv step without invoking run", () => {
  withTmp((dir) => {
    const cfg = project(dir, ONE_MIGRATION);
    writeExe(venvPython(dir));
    const run = runStub();
    const results = bootstrapPython(cfg, { run });

    assert.equal(results[0].step, "venv");
    assert.equal(results[0].ok, true);
    assert.equal(results[0].detail, "already exists");
    assert.equal(
      run.calls.some((c) => c.cmd === PYTHON),
      false,
      "the interpreter must not be asked to build a venv that exists",
    );
    assert.equal(run.calls[0].cmd, venvPython(dir), "the first run call is pip-upgrade");
  });
});

test("a non-executable .venv/bin/python is rebuilt rather than trusted", () => {
  withTmp((dir) => {
    const cfg = project(dir, ONE_MIGRATION);
    writeFile(venvPython(dir), "truncated\n");
    const run = runStub();
    const results = bootstrapPython(cfg, { run });

    assert.equal(results[0].detail !== "already exists", true);
    assert.equal(run.calls[0].cmd, PYTHON);
  });
});

test("an empty migrations directory is not a failure", () => {
  withTmp((dir) => {
    const cfg = project(dir, {});
    const run = runStub();
    const results = bootstrapPython(cfg, { run });

    assert.equal(results.length, 4);
    assert.equal(results[3].step, "migrations");
    assert.equal(results[3].ok, true);
    assert.equal(
      run.calls.some((c) => c.cmd === SQLITE3),
      false,
      "nothing to apply means nothing to run",
    );
  });
});

test("non-.sql entries in the migrations directory are ignored", () => {
  withTmp((dir) => {
    const cfg = project(dir, { ".DS_Store": "junk\n", "README.md": "# notes\n" });
    mkdirSync(join(dir, "data", "migrations", "archive"), { recursive: true });
    const run = runStub();
    const results = bootstrapPython(cfg, { run });

    assert.equal(results[3].ok, true);
    assert.equal(
      run.calls.some((c) => c.cmd === SQLITE3),
      false,
      "a Finder artefact is not a migration",
    );
  });
});

// ── isBootstrapped ────────────────────────────────────────────────────

test("isBootstrapped is true only with both an executable venv python and the db", () => {
  withTmp((dir) => {
    writeExe(venvPython(dir));
    writeFile(join(dir, "data", "trades.sqlite"), "");
    assert.equal(isBootstrapped(cfgFor(dir)), true);
  });
});

test("isBootstrapped is false when the venv interpreter is missing", () => {
  withTmp((dir) => {
    writeFile(join(dir, "data", "trades.sqlite"), "");
    assert.equal(isBootstrapped(cfgFor(dir)), false);
  });
});

test("isBootstrapped is false when the database is missing", () => {
  withTmp((dir) => {
    writeExe(venvPython(dir));
    assert.equal(isBootstrapped(cfgFor(dir)), false);
  });
});

test("isBootstrapped is false for a non-executable venv python and for an empty project", () => {
  withTmp((dir) => {
    writeFile(venvPython(dir), "truncated\n");
    writeFile(join(dir, "data", "trades.sqlite"), "");
    assert.equal(isBootstrapped(cfgFor(dir)), false, "a half-written venv is not a venv");
  });
  withTmp((dir) => {
    assert.equal(isBootstrapped(cfgFor(dir)), false);
  });
  assert.equal(isBootstrapped(cfgFor("/nonexistent/kis-trader-project")), false);
});

// ── defaultRun (the real spawn path) ──────────────────────────────────

test("defaultRun detaches fd 0 so a reader gets EOF instead of blocking", () => {
  // /bin/cat with an inherited terminal would block forever; with stdin
  // detached it sees EOF immediately and exits 0. A hang would trip the
  // timeout and surface as a non-zero code.
  const r = defaultRun("/bin/cat", [], 5000);
  assert.equal(r.code, 0);
  assert.equal(r.out, "");
});

test("defaultRun feeds stdinFile on fd 0 when a file is given", () => {
  withTmp((dir) => {
    const f = writeFile(join(dir, "in.txt"), "hello from a file\n");
    const r = defaultRun("/bin/cat", [], 5000, { stdinFile: f });
    assert.equal(r.code, 0);
    assert.equal(r.out, "hello from a file\n");
  });
});

test("defaultRun captures stderr and the real exit code", () => {
  const r = defaultRun("/bin/sh", ["-c", "echo out; echo boom >&2; exit 3"], 5000);
  assert.equal(r.code, 3);
  assert.match(r.out, /out/);
  assert.match(r.out, /boom/, "stderr is evidence, not noise to discard");
});

test("defaultRun bounds the call with a timeout instead of hanging", () => {
  const started = Date.now();
  const r = defaultRun("/bin/sh", ["-c", "sleep 30"], 300);
  assert.notEqual(r.code, 0);
  assert.ok(Date.now() - started < 10_000, "the call returned rather than hung");
  assert.notEqual(r.out, "", "the timeout is reported, not silently swallowed");
});

test("defaultRun honours cwd", () => {
  withTmp((dir) => {
    const r = defaultRun("/bin/sh", ["-c", "pwd"], 5000, { cwd: dir });
    assert.equal(r.code, 0);
    assert.equal(r.out.trim(), dir);
  });
});

test("defaultRun reports a missing binary as a failure and never throws", () => {
  let r: { code: number; out: string } | undefined;
  assert.doesNotThrow(() => {
    r = defaultRun("/nonexistent/kis-trader/binary", [], 5000);
  });
  assert.notEqual(r?.code, 0);
  assert.match(r?.out ?? "", /ENOENT/);
});

test("defaultRun reports an unreadable stdinFile as a failure and never throws", () => {
  let r: { code: number; out: string } | undefined;
  assert.doesNotThrow(() => {
    r = defaultRun("/bin/cat", [], 5000, { stdinFile: "/nonexistent/kis-trader/migration.sql" });
  });
  assert.notEqual(r?.code, 0);
  assert.match(r?.out ?? "", /ENOENT/);
});
