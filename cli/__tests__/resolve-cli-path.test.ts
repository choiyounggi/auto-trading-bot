import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  isExecutableFile,
  parseNodeVersion,
  pickNvmCandidate,
  resolveNvmDir,
  loginShellWhich,
  resolveCliPath,
  clearResolveCache,
  probeVersion,
  detectAgents,
  type SupportedCli,
} from "../resolve-cli-path.js";

/**
 * Scratch space lives inside the repo (`dist-test/.tmp`, already gitignored)
 * rather than the system temp dir: these fixtures carry the executable bit,
 * and writing +x files under /tmp is what endpoint security flags.
 */
const SCRATCH_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", ".tmp");

function tmp(): string {
  mkdirSync(SCRATCH_ROOT, { recursive: true });
  return mkdtempSync(join(SCRATCH_ROOT, "cli-"));
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

/** Build `<dir>/versions/node/<version>/bin/<cmd>` for each given version. */
function nvmDirWith(dir: string, cmd: string, versions: string[]): string {
  for (const v of versions) {
    writeExe(join(dir, "versions", "node", v, "bin", cmd));
  }
  return dir;
}

function withTmp(fn: (dir: string) => void): void {
  const dir = tmp();
  try {
    fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** Options that make every one of the three stages miss. */
const ALL_MISS = {
  noCache: true,
  wellKnown: () => [],
  env: {},
  loginShell: () => null,
} as const;

// ── normal ────────────────────────────────────────────────────────────

test("stage 1: a well-known candidate that is executable is returned", () => {
  withTmp((dir) => {
    const exe = writeExe(join(dir, "claude"));
    const got = resolveCliPath("claude", {
      noCache: true,
      wellKnown: () => [exe],
    });
    assert.equal(got, exe);
  });
});

test("stage 1 wins over stage 2 and stage 3", () => {
  withTmp((dir) => {
    const wellKnownHit = writeExe(join(dir, "wk", "claude"));
    const home = nvmDirWith(join(dir, "home", ".nvm"), "claude", ["v20.1.0"]);
    let loginShellCalls = 0;

    const got = resolveCliPath("claude", {
      noCache: true,
      wellKnown: () => [wellKnownHit],
      env: { HOME: join(dir, "home") },
      loginShell: () => {
        loginShellCalls++;
        return "/never/used";
      },
    });

    assert.equal(got, wellKnownHit);
    assert.notEqual(got, join(home, "versions", "node", "v20.1.0", "bin", "claude"));
    assert.equal(loginShellCalls, 0, "later stages must not run after a hit");
  });
});

test("stage 2: nvm scan runs when well-known misses, and short-circuits stage 3", () => {
  withTmp((dir) => {
    const home = join(dir, "home");
    nvmDirWith(join(home, ".nvm"), "codex", ["v20.1.0"]);
    let loginShellCalls = 0;

    const got = resolveCliPath("codex", {
      noCache: true,
      wellKnown: () => [],
      env: { HOME: home },
      loginShell: () => {
        loginShellCalls++;
        return null;
      },
    });

    assert.equal(got, join(home, ".nvm", "versions", "node", "v20.1.0", "bin", "codex"));
    assert.equal(loginShellCalls, 0);
  });
});

test("stage 3: the login shell is consulted only after stages 1 and 2 miss", () => {
  withTmp((dir) => {
    const exe = writeExe(join(dir, "gemini"));
    const asked: string[] = [];

    const got = resolveCliPath("gemini", {
      noCache: true,
      wellKnown: () => [],
      env: {},
      loginShell: (cmd) => {
        asked.push(cmd);
        return exe;
      },
    });

    assert.equal(got, exe);
    assert.deepEqual(asked, ["gemini"]);
  });
});

test("a resolved path is cached for the process and cleared by clearResolveCache", () => {
  withTmp((dir) => {
    const exe = writeExe(join(dir, "pi"));
    clearResolveCache();
    try {
      assert.equal(resolveCliPath("pi", { wellKnown: () => [exe] }), exe);
      // Same key, options that would otherwise miss everywhere: cache answers.
      assert.equal(
        resolveCliPath("pi", { wellKnown: () => [], env: {}, loginShell: () => null }),
        exe,
      );
      clearResolveCache();
      assert.equal(
        resolveCliPath("pi", { wellKnown: () => [], env: {}, loginShell: () => null }),
        null,
      );
    } finally {
      clearResolveCache();
    }
  });
});

test("noCache neither reads nor writes the cache", () => {
  withTmp((dir) => {
    const exe = writeExe(join(dir, "pi"));
    clearResolveCache();
    try {
      assert.equal(resolveCliPath("pi", { noCache: true, wellKnown: () => [exe] }), exe);
      // Nothing was stored, so a miss stays a miss.
      assert.equal(resolveCliPath("pi", ALL_MISS), null);
    } finally {
      clearResolveCache();
    }
  });
});

test("parseNodeVersion parses a v-prefixed triple", () => {
  assert.deepEqual(parseNodeVersion("v22.10.0"), [22, 10, 0]);
  assert.deepEqual(parseNodeVersion("22.10.0"), [22, 10, 0]);
});

test("probeVersion returns the first line of the probe output", () => {
  assert.equal(probeVersion("/bin/whatever", () => "1.2.3\nextra"), "1.2.3");
  assert.equal(probeVersion("/bin/whatever", () => "  1.2.3  \r\nextra"), "1.2.3");
});

test("probeVersion hands the binary path to the runner", () => {
  const seen: string[] = [];
  const got = probeVersion("/opt/homebrew/bin/claude", (p) => {
    seen.push(p);
    return "2.0.0";
  });
  assert.equal(got, "2.0.0");
  assert.deepEqual(seen, ["/opt/homebrew/bin/claude"]);
});

test("detectAgents reports every supported CLI with its path and version", () => {
  // /bin/echo stands in for an agent binary: always present, harmless to run,
  // and it keeps the test off any actual LLM CLI installation.
  const got = detectAgents({ noCache: true, wellKnown: () => ["/bin/echo"] });
  assert.deepEqual(Object.keys(got).sort(), ["claude", "codex", "gemini", "pi"]);
  for (const cli of ["claude", "codex", "pi", "gemini"] as SupportedCli[]) {
    const entry = got[cli];
    assert.notEqual(entry, null, `${cli} should be detected`);
    assert.equal(entry!.path, "/bin/echo");
    assert.equal(typeof entry!.version, "string");
    assert.ok(entry!.version!.length > 0 && entry!.version!.length <= 60);
  }
});

// ── error ─────────────────────────────────────────────────────────────

test("resolveCliPath returns null when all three stages miss", () => {
  assert.equal(resolveCliPath("pi", ALL_MISS), null);
});

test("probeVersion returns null instead of throwing when the runner throws", () => {
  let got: string | null = "sentinel";
  assert.doesNotThrow(() => {
    got = probeVersion("/bin/missing", () => {
      throw new Error("ENOENT: no such file or directory");
    });
  });
  assert.equal(got, null);
});

test("probeVersion swallows a timeout-style failure", () => {
  const err = Object.assign(new Error("spawnSync ETIMEDOUT"), { code: "ETIMEDOUT" });
  assert.equal(
    probeVersion("/bin/hangs", () => {
      throw err;
    }),
    null,
  );
});

test("detectAgents reports null for every CLI when nothing resolves", () => {
  const got = detectAgents(ALL_MISS);
  assert.deepEqual(got, { claude: null, codex: null, pi: null, gemini: null });
});

test("loginShellWhich returns null for a command that cannot exist", () => {
  assert.equal(loginShellWhich("kis-trader-no-such-command-9f3a", 3000), null);
});

// ── boundary ──────────────────────────────────────────────────────────

test("parseNodeVersion rejects non-version directory names", () => {
  assert.equal(parseNodeVersion("system"), null);
  assert.equal(parseNodeVersion("lts/*"), null);
  assert.equal(parseNodeVersion(""), null);
  assert.equal(parseNodeVersion("v22.10"), null);
  assert.equal(parseNodeVersion("v22.10.0-rc.1"), null);
});

test("pickNvmCandidate picks the newest version, not the last read", () => {
  withTmp((dir) => {
    const nvm = nvmDirWith(dir, "claude", ["v18.0.0", "v20.1.0"]);
    assert.equal(
      pickNvmCandidate(nvm, "claude"),
      join(nvm, "versions", "node", "v20.1.0", "bin", "claude"),
    );
  });
});

test("pickNvmCandidate compares versions numerically and ignores non-version dirs", () => {
  withTmp((dir) => {
    const nvm = nvmDirWith(dir, "claude", ["v9.0.0", "v10.0.0"]);
    mkdirSync(join(nvm, "versions", "node", "system"), { recursive: true });
    writeExe(join(nvm, "versions", "node", "system", "bin", "claude"));
    assert.equal(
      pickNvmCandidate(nvm, "claude"),
      join(nvm, "versions", "node", "v10.0.0", "bin", "claude"),
    );
  });
});

test("pickNvmCandidate skips versions whose binary is missing or not executable", () => {
  withTmp((dir) => {
    const nvm = nvmDirWith(dir, "claude", ["v18.0.0"]);
    // v20 is newer but its binary lacks the executable bit → v18 must win.
    writePlain(join(nvm, "versions", "node", "v20.1.0", "bin", "claude"));
    // v22 is newest but has no binary at all.
    mkdirSync(join(nvm, "versions", "node", "v22.0.0", "bin"), { recursive: true });
    assert.equal(
      pickNvmCandidate(nvm, "claude"),
      join(nvm, "versions", "node", "v18.0.0", "bin", "claude"),
    );
  });
});

test("pickNvmCandidate returns null for a missing or empty versions directory", () => {
  withTmp((dir) => {
    assert.equal(pickNvmCandidate(join(dir, "nope"), "claude"), null);
    mkdirSync(join(dir, "versions", "node"), { recursive: true });
    assert.equal(pickNvmCandidate(dir, "claude"), null);
  });
});

test("resolveNvmDir accepts only an absolute NVM_DIR and falls back to $HOME", () => {
  assert.equal(resolveNvmDir("/opt/nvm", "/Users/x"), "/opt/nvm");
  assert.equal(resolveNvmDir("relative/nvm", "/Users/x"), join("/Users/x", ".nvm"));
  assert.equal(resolveNvmDir("", "/Users/x"), join("/Users/x", ".nvm"));
  assert.equal(resolveNvmDir(undefined, "/Users/x"), join("/Users/x", ".nvm"));
  assert.equal(resolveNvmDir(undefined, undefined), null);
  assert.equal(resolveNvmDir("relative/nvm", undefined), null);
});

test("isExecutableFile is false for directories, plain files, and missing paths", () => {
  withTmp((dir) => {
    assert.equal(isExecutableFile(dir), false, "a directory is not an executable file");
    assert.equal(isExecutableFile(writePlain(join(dir, "plain"))), false);
    assert.equal(isExecutableFile(join(dir, "does-not-exist")), false);
    assert.equal(isExecutableFile(""), false);
    assert.equal(isExecutableFile(writeExe(join(dir, "exe"))), true);
  });
});

test("probeVersion caps a long version line at exactly 60 characters", () => {
  const long = "x".repeat(200);
  const got = probeVersion("/bin/verbose", () => long);
  assert.equal(got!.length, 60);
  assert.equal(got, "x".repeat(60));
});

test("probeVersion returns null for empty or whitespace-only output", () => {
  assert.equal(probeVersion("/bin/quiet", () => ""), null);
  assert.equal(probeVersion("/bin/quiet", () => "\n\n"), null);
  assert.equal(probeVersion("/bin/quiet", () => "   \n1.0.0"), null);
});

test("a nvm directory that does not exist leaves stage 3 as the only chance", () => {
  withTmp((dir) => {
    const exe = writeExe(join(dir, "claude"));
    const got = resolveCliPath("claude", {
      noCache: true,
      wellKnown: () => [],
      env: { HOME: join(dir, "no-such-home") },
      loginShell: () => exe,
    });
    assert.equal(got, exe);
  });
});
