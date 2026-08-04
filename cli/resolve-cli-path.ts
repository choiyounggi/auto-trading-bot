/**
 * Resolve a local LLM CLI binary path across heterogeneous environments.
 *
 * kis-trader runs its agent calls from launchd jobs, and a daemon-spawned
 * process inherits a minimal PATH with no rc files — the npm/brew/nvm shims an
 * interactive shell sees are simply absent there. So nothing here calls a bare
 * command name: every stage produces an absolute path, and a miss is reported
 * as `null` rather than deferred to a PATH lookup at call time.
 *
 * Resolution order (first hit wins):
 *   1. Well-known absolute paths per CLI (`~/.local/bin`, Homebrew, etc.).
 *   2. nvm directory scan — newest node version's `bin/<cmd>`.
 *   3. Login-shell fallback — `/bin/zsh -l -i -c 'command -v <cmd>'`,
 *      isolated by a sentinel prefix so `.zshrc` banner output cannot
 *      contaminate the parsed result.
 *
 * Results are cached for the process lifetime: a login-shell spawn is
 * expensive and the filesystem does not move under a running bot.
 *
 * Ported from cliclaw's `lib/resolve-cli-path.ts` (Bun) — the module was
 * already pure `node:` API, so behaviour is unchanged.
 */

import { existsSync, statSync, readdirSync, type Dirent } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";

const LOGIN_SHELL_TIMEOUT_MS = 3000;
const WHICH_SENTINEL = "__WHICH__=";

/** A version banner longer than this is noise, not a version. */
const VERSION_MAX_LEN = 60;

/** Upper bound on a `--version` probe, so a wedged CLI cannot stall startup. */
const PROBE_TIMEOUT_MS = 5000;

export type SupportedCli = "claude" | "codex" | "pi" | "gemini";

const SUPPORTED_CLIS: readonly SupportedCli[] = ["claude", "codex", "pi", "gemini"];

/** Per-process cache so repeated lookups (startup + reload) stay cheap. */
const resolveCache = new Map<string, string>();

/** True iff `p` is a regular file with at least one executable bit set. */
export function isExecutableFile(p: string): boolean {
  try {
    const st = statSync(p);
    if (!st.isFile()) return false;
    // POSIX execute bits: owner/group/other.
    return (st.mode & 0o111) !== 0;
  } catch {
    return false;
  }
}

function wellKnownCandidates(cmd: SupportedCli): string[] {
  const home = homedir();
  // Order matters: user-local installs (~/.local/bin) precede system-wide
  // ones so a user who pinned a specific version via npm wins over an older
  // brew bottle.
  switch (cmd) {
    case "claude":
      return [
        join(home, ".local/bin/claude"),
        join(home, ".claude/local/claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
      ];
    case "codex":
      return [
        join(home, ".local/bin/codex"),
        "/usr/local/bin/codex",
        "/opt/homebrew/bin/codex",
      ];
    case "pi":
      return [
        join(home, ".local/bin/pi"),
        "/usr/local/bin/pi",
        "/opt/homebrew/bin/pi",
      ];
    case "gemini":
      return [
        join(home, ".local/bin/gemini"),
        "/usr/local/bin/gemini",
        "/opt/homebrew/bin/gemini",
      ];
  }
}

/** Parse `v22.10.0` → `[22, 10, 0]`. Returns null on malformed input so
 *  callers can skip directories like `system` or `lts/*`. */
export function parseNodeVersion(name: string): [number, number, number] | null {
  const m = name.match(/^v?(\d+)\.(\d+)\.(\d+)$/);
  if (!m) return null;
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

function compareVersion(
  a: [number, number, number],
  b: [number, number, number],
): number {
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return 0;
}

/** Pick the newest nvm-managed node version that has `<cmd>` under its
 *  `bin/` directory. */
export function pickNvmCandidate(nvmDir: string, cmd: string): string | null {
  const versionsDir = join(nvmDir, "versions", "node");
  let entries: Dirent[];
  try {
    entries = readdirSync(versionsDir, { withFileTypes: true }) as Dirent[];
  } catch {
    return null;
  }
  const found: { version: [number, number, number]; path: string }[] = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const v = parseNodeVersion(entry.name);
    if (!v) continue;
    const candidate = join(versionsDir, entry.name, "bin", cmd);
    if (isExecutableFile(candidate)) found.push({ version: v, path: candidate });
  }
  if (found.length === 0) return null;
  found.sort((a, b) => compareVersion(a.version, b.version));
  return found[found.length - 1].path;
}

/** Resolve `$NVM_DIR`, falling back to `$HOME/.nvm`. A non-absolute
 *  `NVM_DIR` is rejected — relative paths in env vars are nearly always
 *  unintended and would resolve against the bot's cwd. */
export function resolveNvmDir(
  nvmEnv: string | undefined,
  homeEnv: string | undefined,
): string | null {
  if (nvmEnv && nvmEnv.startsWith("/")) return nvmEnv;
  if (homeEnv) return join(homeEnv, ".nvm");
  return null;
}

/** Ask an interactive login shell where `cmd` lives. Sentinel-prefixed so
 *  `.zshrc` banner output (figlet, motd, etc.) is filtered out. stdin is
 *  detached and the call is bounded by a timeout, so an rc file that prompts
 *  fails fast instead of hanging the caller. */
export function loginShellWhich(
  cmd: string,
  timeoutMs: number = LOGIN_SHELL_TIMEOUT_MS,
): string | null {
  // Built via concatenation to keep the TS tokenizer out of the nested
  // shell quoting. $1 is the command name, passed positionally so a CLI
  // name containing shell metacharacters could never reach the parser.
  const script =
    "v=$(command -v -- \"$1\"); printf '" + WHICH_SENTINEL + "%s\\n' \"$v\"";
  try {
    const stdout = execFileSync(
      "/bin/zsh",
      ["-l", "-i", "-c", script, "zsh", cmd],
      {
        encoding: "utf8",
        timeout: timeoutMs,
        stdio: ["ignore", "pipe", "ignore"],
      },
    );
    for (const line of stdout.split("\n")) {
      if (!line.startsWith(WHICH_SENTINEL)) continue;
      const value = line.slice(WHICH_SENTINEL.length).trim();
      if (value && isExecutableFile(value)) return value;
    }
  } catch {
    // SIGTERM from timeout, non-zero exit, or shell not found — all map to "no result".
  }
  return null;
}

export interface ResolveOptions {
  /** Skip the in-process cache (testing only). */
  noCache?: boolean;
  /** Override env vars (testing only). */
  env?: NodeJS.ProcessEnv;
  /** Override the login-shell fallback (testing only). */
  loginShell?: (cmd: string) => string | null;
  /** Override the well-known candidate list (testing only). When provided,
   *  the host's real system binaries are not consulted. */
  wellKnown?: (cmd: SupportedCli) => string[];
}

export function resolveCliPath(
  cmd: SupportedCli,
  opts: ResolveOptions = {},
): string | null {
  if (!opts.noCache) {
    const hit = resolveCache.get(cmd);
    if (hit) return hit;
  }
  const env = opts.env ?? process.env;

  const wellKnown = (opts.wellKnown ?? wellKnownCandidates)(cmd);
  for (const candidate of wellKnown) {
    if (isExecutableFile(candidate)) {
      if (!opts.noCache) resolveCache.set(cmd, candidate);
      return candidate;
    }
  }

  const nvmDir = resolveNvmDir(env.NVM_DIR, env.HOME);
  if (nvmDir && existsSync(nvmDir)) {
    const nvmHit = pickNvmCandidate(nvmDir, cmd);
    if (nvmHit) {
      if (!opts.noCache) resolveCache.set(cmd, nvmHit);
      return nvmHit;
    }
  }

  const shellHit = (opts.loginShell ?? loginShellWhich)(cmd);
  if (shellHit) {
    if (!opts.noCache) resolveCache.set(cmd, shellHit);
    return shellHit;
  }

  return null;
}

/** Clear the resolution cache. Tests use this; production has no reason
 *  to call it — the underlying filesystem rarely changes mid-run. */
export function clearResolveCache(): void {
  resolveCache.clear();
}

/** Runs a version probe for `binPath` and returns its stdout. */
export type ProbeRunner = (binPath: string) => string;

/**
 * Ask a binary for its version.
 *
 * stdin is `"ignore"` — an agent CLI that decides it wants input would
 * otherwise block forever on a fd 0 it inherited from us — and the call is
 * bounded by a timeout so a binary that ignores that still cannot wedge the
 * caller. stderr is discarded so a startup warning banner cannot be mistaken
 * for the version.
 */
const defaultProbe: ProbeRunner = (binPath) =>
  execFileSync(binPath, ["--version"], {
    encoding: "utf8",
    timeout: PROBE_TIMEOUT_MS,
    stdio: ["ignore", "pipe", "ignore"],
  });

/**
 * First line of `<binPath> --version`, capped at 60 characters.
 *
 * Returns `null` on any failure — missing binary, non-zero exit, timeout, or
 * output with no content — and never throws: a version string is decoration
 * for `doctor` output, never a reason to fail a caller.
 */
export function probeVersion(
  binPath: string,
  run: ProbeRunner = defaultProbe,
): string | null {
  let out: string;
  try {
    out = run(binPath);
  } catch {
    return null;
  }
  if (typeof out !== "string") return null;
  const firstLine = out.split("\n", 1)[0].trim();
  if (!firstLine) return null;
  return firstLine.slice(0, VERSION_MAX_LEN);
}

export interface DetectedAgent {
  path: string;
  version: string | null;
}

/**
 * Resolve every supported CLI at once, with its version where the binary
 * answered. `null` means "not installed on this machine" — the caller decides
 * whether that is fatal.
 */
export function detectAgents(
  opts: ResolveOptions = {},
): Record<SupportedCli, DetectedAgent | null> {
  const result = {} as Record<SupportedCli, DetectedAgent | null>;
  for (const cli of SUPPORTED_CLIS) {
    const path = resolveCliPath(cli, opts);
    result[cli] = path === null ? null : { path, version: probeVersion(path) };
  }
  return result;
}
