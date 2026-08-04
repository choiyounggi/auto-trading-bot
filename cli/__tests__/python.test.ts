import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  pythonVersion,
  isSupported,
  findPython,
  PYTHON_CANDIDATES,
} from "../python.js";

/**
 * Scratch space lives inside the repo (`dist-test/.tmp`, already gitignored) rather
 * than the system temp dir: these fixtures carry the executable bit, and
 * writing +x files under /tmp is what endpoint security flags.
 *
 * No test here consults a real interpreter — `version` and `loginShell` are
 * always injected, and the fixtures below are only ever stat'ed, never run.
 */
const SCRATCH_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", ".tmp");

function tmp(): string {
  mkdirSync(SCRATCH_ROOT, { recursive: true });
  return mkdtempSync(join(SCRATCH_ROOT, "py-"));
}

/** Create a file carrying an executable bit. Never executed — only stat'ed. */
function writeExe(path: string): string {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  return path;
}

function writePlain(path: string): string {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, "not executable\n", { mode: 0o644 });
  return path;
}

function withTmp(fn: (dir: string) => void): void {
  const dir = tmp();
  try {
    fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/**
 * A `pythonVersion` stub driven by a path→version map, recording every path it
 * was asked about so a test can assert what was *not* probed.
 */
function versionStub(
  table: Record<string, [number, number] | null>,
): ((p: string) => [number, number] | null) & { calls: string[] } {
  const calls: string[] = [];
  const fn = (p: string): [number, number] | null => {
    calls.push(p);
    return table[p] ?? null;
  };
  return Object.assign(fn, { calls });
}

/** A `loginShell` stub driven by a command→path map, recording the order asked. */
function loginShellStub(
  table: Record<string, string | null>,
): ((cmd: string) => string | null) & { calls: string[] } {
  const calls: string[] = [];
  const fn = (cmd: string): string | null => {
    calls.push(cmd);
    return table[cmd] ?? null;
  };
  return Object.assign(fn, { calls });
}

/** Options that make every stage miss unless a test overrides one. */
const ALL_MISS = {
  candidates: [] as string[],
  version: () => null,
  loginShell: () => null,
} as const;

// ── pythonVersion: normal ─────────────────────────────────────────────

test("pythonVersion parses a stub reporting 3.11", () => {
  assert.deepEqual(
    pythonVersion("/some/python3.11", () => "3.11\n"),
    [3, 11],
  );
});

test("pythonVersion parses 3.13 and returns numbers, not strings", () => {
  const v = pythonVersion("/some/python3.13", () => "3.13\n");
  assert.deepEqual(v, [3, 13]);
  assert.equal(typeof v?.[0], "number");
  assert.equal(typeof v?.[1], "number");
});

test("pythonVersion tolerates surrounding whitespace and trailing output", () => {
  assert.deepEqual(
    pythonVersion("/some/python", () => "  3.12  \nignored trailer\n"),
    [3, 12],
  );
});

test("pythonVersion passes the binary path through to the runner", () => {
  const seen: string[] = [];
  pythonVersion("/opt/homebrew/bin/python3.12", (p) => {
    seen.push(p);
    return "3.12\n";
  });
  assert.deepEqual(seen, ["/opt/homebrew/bin/python3.12"]);
});

// ── pythonVersion: error ──────────────────────────────────────────────

test("pythonVersion returns null when the runner throws, and does not throw", () => {
  let result: [number, number] | null | undefined;
  assert.doesNotThrow(() => {
    result = pythonVersion("/missing/python3", () => {
      throw new Error("ENOENT: no such file or directory");
    });
  });
  assert.equal(result, null);
});

test("pythonVersion returns null when the default runner hits a missing binary", () => {
  // Exercises the real execFileSync path against a path that cannot exist,
  // proving the default runner's failure is swallowed rather than thrown.
  assert.doesNotThrow(() => {
    assert.equal(pythonVersion("/nonexistent/kis-trader/python3"), null);
  });
});

test("pythonVersion returns null on unparseable output", () => {
  assert.equal(pythonVersion("/some/python", () => "Python 3.11.9\n"), null);
  assert.equal(pythonVersion("/some/python", () => "three.eleven\n"), null);
  assert.equal(pythonVersion("/some/python", () => "3\n"), null);
});

// ── pythonVersion: boundary ───────────────────────────────────────────

test("pythonVersion returns null on empty or whitespace-only output", () => {
  assert.equal(pythonVersion("/some/python", () => ""), null);
  assert.equal(pythonVersion("/some/python", () => "\n\n"), null);
  assert.equal(pythonVersion("/some/python", () => "   "), null);
});

test("pythonVersion returns null when the runner yields a non-string", () => {
  assert.equal(
    pythonVersion("/some/python", (() => undefined) as unknown as (p: string) => string),
    null,
  );
  assert.equal(
    pythonVersion("/some/python", (() => Buffer.from("3.11")) as unknown as (p: string) => string),
    null,
  );
});

test("pythonVersion accepts a two-digit minor", () => {
  assert.deepEqual(pythonVersion("/some/python", () => "3.10\n"), [3, 10]);
});

// ── isSupported: boundary ─────────────────────────────────────────────

test("isSupported gates on the 3.11–3.13 window", () => {
  assert.equal(isSupported([3, 10]), false, "3.10 is below the floor");
  assert.equal(isSupported([3, 11]), true, "3.11 is the floor");
  assert.equal(isSupported([3, 12]), true);
  assert.equal(isSupported([3, 13]), true, "3.13 is the ceiling");
  // The exact case live on the author's machine: python3 is 3.14.6.
  assert.equal(isSupported([3, 14]), false, "3.14 is above the ceiling");
});

test("isSupported rejects null", () => {
  assert.equal(isSupported(null), false);
});

test("isSupported rejects other majors", () => {
  assert.equal(isSupported([2, 7]), false);
  assert.equal(isSupported([2, 11]), false);
  assert.equal(isSupported([4, 11]), false);
  assert.equal(isSupported([4, 0]), false);
});

// ── PYTHON_CANDIDATES ─────────────────────────────────────────────────

test("PYTHON_CANDIDATES lists the framework, homebrew and local prefixes newest-first", () => {
  assert.deepEqual(PYTHON_CANDIDATES, [
    "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13",
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12",
    "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11",
    "/opt/homebrew/bin/python3.13",
    "/opt/homebrew/bin/python3.12",
    "/opt/homebrew/bin/python3.11",
    "/usr/local/bin/python3.13",
    "/usr/local/bin/python3.12",
    "/usr/local/bin/python3.11",
  ]);
});

test("every PYTHON_CANDIDATES entry is an absolute path, never a bare name", () => {
  assert.equal(PYTHON_CANDIDATES.length, 9);
  for (const c of PYTHON_CANDIDATES) {
    assert.ok(c.startsWith("/"), `${c} is not absolute`);
  }
});

// ── findPython: normal ────────────────────────────────────────────────

test("findPython returns the second candidate when it is executable and reports 3.12", () => {
  withTmp((dir) => {
    const first = join(dir, "missing", "python3.13");
    const second = writeExe(join(dir, "python3.12"));
    const version = versionStub({ [second]: [3, 12] });
    assert.equal(
      findPython({
        candidates: [first, second],
        version,
        loginShell: () => null,
      }),
      second,
    );
    // The absent first candidate is never probed.
    assert.deepEqual(version.calls, [second]);
  });
});

test("findPython returns the first qualifying candidate and probes no further", () => {
  withTmp((dir) => {
    const first = writeExe(join(dir, "python3.13"));
    const second = writeExe(join(dir, "python3.11"));
    const version = versionStub({ [first]: [3, 13], [second]: [3, 11] });
    assert.equal(
      findPython({ candidates: [first, second], version, loginShell: () => null }),
      first,
    );
    assert.deepEqual(version.calls, [first]);
  });
});

test("findPython falls back to the login shell when no candidate qualifies", () => {
  withTmp((dir) => {
    const shellHit = writeExe(join(dir, "python3.11"));
    const loginShell = loginShellStub({ "python3.11": shellHit });
    assert.equal(
      findPython({
        candidates: [],
        version: versionStub({ [shellHit]: [3, 11] }),
        loginShell,
      }),
      shellHit,
    );
    assert.deepEqual(loginShell.calls, ["python3.13", "python3.12", "python3.11"]);
  });
});

test("findPython accepts a bare python3 only after reading its version", () => {
  withTmp((dir) => {
    const hit = writeExe(join(dir, "python3"));
    const loginShell = loginShellStub({ python3: hit });
    const version = versionStub({ [hit]: [3, 11] });
    assert.equal(findPython({ candidates: [], version, loginShell }), hit);
    // python3 is asked last, and only after its version was read.
    assert.deepEqual(loginShell.calls, [
      "python3.13",
      "python3.12",
      "python3.11",
      "python3",
    ]);
    assert.deepEqual(version.calls, [hit]);
  });
});

test("findPython prefers a qualifying candidate over the login shell", () => {
  withTmp((dir) => {
    const candidate = writeExe(join(dir, "python3.11"));
    const loginShell = loginShellStub({ python3: "/usr/bin/python3" });
    assert.equal(
      findPython({
        candidates: [candidate],
        version: versionStub({ [candidate]: [3, 11] }),
        loginShell,
      }),
      candidate,
    );
    assert.deepEqual(loginShell.calls, [], "login shell must not be spawned");
  });
});

// ── findPython: error ─────────────────────────────────────────────────

test("findPython returns null with no candidates and a login shell that finds nothing", () => {
  const loginShell = loginShellStub({});
  assert.equal(findPython({ candidates: [], version: () => null, loginShell }), null);
  assert.deepEqual(loginShell.calls, [
    "python3.13",
    "python3.12",
    "python3.11",
    "python3",
  ]);
});

test("findPython returns null when every stage misses", () => {
  assert.equal(findPython(ALL_MISS), null);
});

test("findPython returns null when a candidate's version probe fails", () => {
  withTmp((dir) => {
    const exe = writeExe(join(dir, "python3.11"));
    assert.equal(
      findPython({
        candidates: [exe],
        version: () => null, // probe threw / unparseable
        loginShell: () => null,
      }),
      null,
    );
  });
});

// ── findPython: boundary ──────────────────────────────────────────────

test("findPython skips an executable 3.14 candidate and returns the later 3.11 one", () => {
  withTmp((dir) => {
    const tooNew = writeExe(join(dir, "python3.14"));
    const usable = writeExe(join(dir, "python3.11"));
    const version = versionStub({ [tooNew]: [3, 14], [usable]: [3, 11] });
    assert.equal(
      findPython({ candidates: [tooNew, usable], version, loginShell: () => null }),
      usable,
      "version gating must win over first-executable-wins",
    );
    assert.deepEqual(version.calls, [tooNew, usable], "the 3.14 binary was read, then rejected");
  });
});

test("findPython rejects an unsupported bare python3 from the login shell", () => {
  withTmp((dir) => {
    // Mirrors this machine: `python3` resolves, but reports 3.14.
    const tooNew = writeExe(join(dir, "python3"));
    const version = versionStub({ [tooNew]: [3, 14] });
    assert.equal(
      findPython({
        candidates: [],
        version,
        loginShell: loginShellStub({ python3: tooNew }),
      }),
      null,
    );
    assert.deepEqual(version.calls, [tooNew], "it was probed, not blindly accepted");
  });
});

test("findPython skips a non-executable candidate without probing it", () => {
  withTmp((dir) => {
    const plain = writePlain(join(dir, "python3.13"));
    const exe = writeExe(join(dir, "python3.11"));
    const version = versionStub({ [plain]: [3, 13], [exe]: [3, 11] });
    assert.equal(
      findPython({ candidates: [plain, exe], version, loginShell: () => null }),
      exe,
    );
    assert.ok(!version.calls.includes(plain), "a non-executable file must not be run");
  });
});

test("findPython handles an empty candidate list and an empty-string candidate", () => {
  const version = versionStub({});
  assert.equal(findPython({ candidates: [], version, loginShell: () => null }), null);
  assert.equal(findPython({ candidates: [""], version, loginShell: () => null }), null);
  assert.deepEqual(version.calls, [], "neither case reaches the version probe");
});

test("findPython skips a login-shell 3.13 hit that is too new and keeps looking", () => {
  withTmp((dir) => {
    const tooNew = writeExe(join(dir, "python3.13-fake"));
    const usable = writeExe(join(dir, "python3.11"));
    const loginShell = loginShellStub({
      "python3.13": tooNew,
      "python3.11": usable,
    });
    const version = versionStub({ [tooNew]: [3, 14], [usable]: [3, 11] });
    assert.equal(findPython({ candidates: [], version, loginShell }), usable);
    assert.deepEqual(loginShell.calls, ["python3.13", "python3.12", "python3.11"]);
  });
});
