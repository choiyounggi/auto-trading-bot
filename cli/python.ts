/**
 * Locate a Python interpreter the trading engine can actually run on.
 *
 * The engine requires `>=3.11` but is only validated on 3.11–3.13, so
 * discovery is a *version gate*, not a name lookup: every candidate is asked
 * what it is before it is accepted. A bare `python3` is never taken on faith —
 * on this machine it is 3.14.6, which would install fine and fail at runtime.
 *
 * Discovery order (first interpreter reporting 3.11–3.13 wins):
 *   1. Well-known absolute paths — python.org framework builds, Homebrew,
 *      `/usr/local` — newest supported minor first within each prefix.
 *   2. Login-shell lookup for `python3.13`, `python3.12`, `python3.11`, then
 *      `python3`, each still version-gated.
 *
 * The result is an absolute path because task 07 writes it into a launchd
 * plist's ProgramArguments and task 08 builds a venv from it. A launchd job
 * inherits a minimal PATH and loads no rc files, so a version-manager shim or
 * a bare name that resolves in your terminal resolves to nothing — or to a
 * different interpreter — under the daemon.
 */

import { execFileSync } from "node:child_process";

import { isExecutableFile, loginShellWhich } from "./resolve-cli-path.js";

/** Upper bound on a version probe, so a wedged interpreter cannot stall startup. */
const VERSION_TIMEOUT_MS = 5000;

/** Lowest and highest minor of Python 3 the engine is validated against. */
const MIN_MINOR = 11;
const MAX_MINOR = 13;

/** Runs a version probe for `binPath` and returns its stdout. */
export type PythonRunner = (binPath: string) => string;

/**
 * Ask the interpreter itself for its version rather than trusting its filename:
 * `/usr/local/bin/python3.11` may well be a symlink to something else.
 *
 * stdin is `"ignore"` so an interpreter that decides it wants input cannot
 * block on a fd it inherited from us, the call is bounded by a timeout, and
 * stderr is discarded so a startup warning cannot be mistaken for the version.
 */
const defaultRun: PythonRunner = (binPath) =>
  execFileSync(
    binPath,
    ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
    {
      encoding: "utf8",
      timeout: VERSION_TIMEOUT_MS,
      stdio: ["ignore", "pipe", "ignore"],
    },
  );

/**
 * `[major, minor]` as reported by `binPath`, or `null`.
 *
 * Returns `null` on every failure — missing binary, non-zero exit, timeout,
 * output that is not `"<major>.<minor>"` — and never throws: an interpreter
 * that will not answer is simply not a candidate.
 */
export function pythonVersion(
  binPath: string,
  run: PythonRunner = defaultRun,
): [number, number] | null {
  let out: string;
  try {
    out = run(binPath);
  } catch {
    return null;
  }
  if (typeof out !== "string") return null;
  const m = /^(\d+)\.(\d+)$/.exec(out.split("\n", 1)[0].trim());
  if (!m) return null;
  return [Number(m[1]), Number(m[2])];
}

/**
 * Absolute paths probed before falling back to the login shell.
 *
 * Newest supported minor first within each prefix, and python.org framework
 * builds ahead of Homebrew: a `brew upgrade python` can retire the interpreter
 * a venv was built against, while a framework build stays put.
 */
export const PYTHON_CANDIDATES: string[] = [
  "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13",
  "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12",
  "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11",
  "/opt/homebrew/bin/python3.13",
  "/opt/homebrew/bin/python3.12",
  "/opt/homebrew/bin/python3.11",
  "/usr/local/bin/python3.13",
  "/usr/local/bin/python3.12",
  "/usr/local/bin/python3.11",
];

/** Command names asked of the login shell, most specific last-resort last. */
const SHELL_LOOKUPS: readonly string[] = [
  "python3.13",
  "python3.12",
  "python3.11",
  "python3",
];

/** True iff `v` is a Python 3 minor the engine is validated against. */
export function isSupported(v: [number, number] | null): boolean {
  if (!v) return false;
  return v[0] === 3 && v[1] >= MIN_MINOR && v[1] <= MAX_MINOR;
}

export interface FindPythonOptions {
  /** Override the well-known candidate list (testing only). When provided,
   *  the host's real interpreters are not consulted. */
  candidates?: string[];
  /** Override the version probe (testing only). */
  version?: typeof pythonVersion;
  /** Override the login-shell fallback (testing only). */
  loginShell?: (cmd: string) => string | null;
}

/**
 * Absolute path of an interpreter reporting 3.11–3.13, or `null`.
 *
 * `null` means "no usable interpreter here" — the caller reports that and
 * stops. It never degrades to a bare `python3`, because a name that resolves
 * under your login shell is not the binary a launchd job would get.
 */
export function findPython(opts: FindPythonOptions = {}): string | null {
  const candidates = opts.candidates ?? PYTHON_CANDIDATES;
  const version = opts.version ?? pythonVersion;
  const loginShell = opts.loginShell ?? loginShellWhich;

  for (const candidate of candidates) {
    if (!isExecutableFile(candidate)) continue;
    if (isSupported(version(candidate))) return candidate;
  }

  for (const cmd of SHELL_LOOKUPS) {
    const hit = loginShell(cmd);
    if (hit && isSupported(version(hit))) return hit;
  }

  return null;
}
