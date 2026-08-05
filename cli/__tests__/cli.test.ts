/**
 * Tests for the CLI entry point's pure exports.
 *
 * `main()` is deliberately never called: it spawns launchctl, npm and the
 * project's Python interpreter, so running it here would mutate the machine
 * the tests happen to run on. Importing the module is safe because `index.ts`
 * only dispatches when it is the process entry point (`process.argv[1]`).
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { COMMANDS, parseArgv, usage } from "../index.js";

const EXPECTED_NAMES = [
  "init",
  "doctor",
  "start",
  "logs",
  "install-jobs",
  "uninstall-jobs",
  "status",
  "upgrade",
  "help",
];

// ── normal ────────────────────────────────────────────────────────────

test("parseArgv splits the command from the remaining arguments", () => {
  assert.deepEqual(parseArgv(["doctor", "--json"]), {
    cmd: "doctor",
    rest: ["--json"],
  });
});

test("parseArgv preserves the order and count of the remaining arguments", () => {
  assert.deepEqual(parseArgv(["logs", "monitor", "--err"]), {
    cmd: "logs",
    rest: ["monitor", "--err"],
  });
});

test("parseArgv leaves an unknown command untouched for main() to reject", () => {
  assert.deepEqual(parseArgv(["totally-unknown"]), {
    cmd: "totally-unknown",
    rest: [],
  });
});

test("usage lists every command in COMMANDS", () => {
  const text = usage("1.0.0");
  // Iterating COMMANDS is the point: a command added without a help line fails
  // here, so help can never drift from the dispatch table.
  for (const c of COMMANDS) {
    assert.ok(
      text.includes(c.name),
      `usage() is missing the command "${c.name}"`,
    );
    assert.ok(
      text.includes(c.summary),
      `usage() is missing the summary for "${c.name}"`,
    );
  }
});

test("usage names the version it was given", () => {
  assert.ok(usage("1.2.3").includes("1.2.3"));
});

test("usage documents the environment variables", () => {
  const text = usage("1.0.0");
  assert.ok(text.includes("Env:"));
  assert.ok(text.includes("KIS_TRADER_HOME"));
  assert.ok(text.includes("KIS_MODE"));
});

// ── error ─────────────────────────────────────────────────────────────

test("parseArgv with no arguments yields the help command, not an empty one", () => {
  const parsed = parseArgv([]);
  assert.equal(parsed.cmd, "help");
  assert.deepEqual(parsed.rest, []);
});

test("parseArgv maps the --help and -h flags onto the help command", () => {
  assert.equal(parseArgv(["--help"]).cmd, "help");
  assert.equal(parseArgv(["-h"]).cmd, "help");
});

test("parseArgv treats a blank first argument as help", () => {
  assert.equal(parseArgv([""]).cmd, "help");
  assert.equal(parseArgv(["   "]).cmd, "help");
});

// ── boundary ──────────────────────────────────────────────────────────

test("COMMANDS marks the streaming commands non-interactive and the rest interactive", () => {
  const byName = new Map(COMMANDS.map((c) => [c.name, c]));
  // start and logs stream forever; the banner would scroll their first output
  // off screen, so they must not print it.
  assert.equal(byName.get("start")?.interactive, false);
  assert.equal(byName.get("logs")?.interactive, false);
  assert.equal(byName.get("init")?.interactive, true);
  assert.equal(byName.get("doctor")?.interactive, true);
});

test("COMMANDS covers exactly the nine documented commands, in order", () => {
  assert.deepEqual(
    COMMANDS.map((c) => c.name),
    EXPECTED_NAMES,
  );
});

test("every COMMANDS entry has a non-empty summary", () => {
  for (const c of COMMANDS) {
    assert.equal(typeof c.summary, "string");
    assert.notEqual(c.summary.trim(), "", `"${c.name}" has an empty summary`);
  }
});

test("usage with an empty version still lists every command", () => {
  const text = usage("");
  for (const c of COMMANDS) {
    assert.ok(text.includes(c.name), `usage("") is missing "${c.name}"`);
  }
  assert.ok(text.endsWith("\n"));
});

test("parseArgv keeps arguments that look like the command itself", () => {
  assert.deepEqual(parseArgv(["start", "monitor"]), {
    cmd: "start",
    rest: ["monitor"],
  });
});
