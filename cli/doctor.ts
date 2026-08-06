/**
 * `kis-trader doctor` — diagnose an installation.
 *
 * Every check answers one question about the machine, and each answer carries a
 * remediation hint whenever it is not a pass: a diagnostic that says "broken"
 * without saying "run this" just moves the guessing to the user.
 *
 * Three rules shape the module:
 *
 *   1. **Observe, never infer.** A job is `loaded` because `launchctl list`
 *      showed it (via `jobStatus`), not because an install command once exited
 *      0; KIS connectivity is a real round trip, not a stored-credential count.
 *      (WIKI platforms-processes-background-services, rule 4.)
 *   2. **The config schema is the inventory.** `JOB_KEYS` drives the per-job
 *      checks, so a job added to the schema cannot be silently unchecked here.
 *      (WIKI infrastructure-config-environment-config, rule 4.)
 *   3. **Nothing crashes.** A machine with no config, a locked keychain, a
 *      missing directory or a Python process that dies mid-probe all come back
 *      as checks. `doctor` is what you run *because* the install is broken.
 *
 * Credentials never enter this process: the KIS round trip is delegated to
 * `python -m src.broker.probe`, which reads the keychain itself and prints only
 * a masked account number (D18). This module spawns it and maps its `reason`.
 *
 * Every collaborator is injectable (`DoctorDeps`) — that is what lets the tests
 * cover a locked keychain, a stale signal directory and a rejected app key
 * without touching the machine they run on.
 */

import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import {
  JOB_KEYS,
  loadConfig as defaultLoadConfig,
  type Config,
  type JobName,
  type ParseResult,
} from "./config.js";
import { venvPython } from "./bootstrap.js";
import {
  BRAVE_KEY_ACCOUNT,
  KIS_SERVICE,
  KRX_ID_ACCOUNT,
  KRX_PW_ACCOUNT,
  SIGNAL_SERVICE,
  TELEGRAM_CHATID_ACCOUNT,
  TELEGRAM_SERVICE,
  TELEGRAM_TOKEN_ACCOUNT,
  KeychainLockedError,
  keychainHas as defaultKeychainHas,
  kisAccount,
} from "./keychain.js";
import {
  jobStatus as defaultJobStatus,
  labelFor,
  plistPath,
  type JobKey,
  type JobStatus,
} from "./launchd.js";
import { isSupported, pythonVersion as defaultPythonVersion } from "./python.js";
import {
  detectAgents as defaultDetectAgents,
  isExecutableFile as defaultIsExecutableFile,
  type DetectedAgent,
  type SupportedCli,
} from "./resolve-cli-path.js";

export type CheckStatus = "pass" | "warn" | "fail";

export interface Check {
  name: string;
  status: CheckStatus;
  detail: string;
  /** What to do about it. Present on every non-pass check. */
  hint?: string;
}

/** One file in the signal directory, reduced to what freshness needs. */
export interface SignalEntry {
  name: string;
  mtimeMs: number;
}

export interface ProbeSpawn {
  cwd: string;
  env: NodeJS.ProcessEnv;
  timeoutMs: number;
}

/** What the probe process left behind. `code` is `-1` when it never ran. */
export interface ProbeOutcome {
  code: number;
  stdout: string;
  stderr: string;
}

export type ProbeRunner = (
  command: string,
  args: string[],
  opts: ProbeSpawn,
) => ProbeOutcome;

export interface DoctorDeps {
  loadConfig: () => ParseResult;
  pythonVersion: (binPath: string) => [number, number] | null;
  isExecutableFile: (path: string) => boolean;
  fileExists: (path: string) => boolean;
  /** Files in `dir`, or `null` when the directory does not exist. */
  signalEntries: (dir: string) => SignalEntry[] | null;
  keychainHas: (service: string, account: string) => boolean;
  detectAgents: () => Record<SupportedCli, DetectedAgent | null>;
  jobStatus: (job: JobKey) => JobStatus;
  runProbe: ProbeRunner;
  /** Injected clock — signal freshness must not depend on wall time. */
  now: () => number;
}

/** A signal older than this is stale: three sessions with no new signal. */
const STALE_SIGNAL_MS = 72 * 60 * 60 * 1000;

/** The probe does one KIS round trip; beyond this it is wedged, not slow. */
const PROBE_TIMEOUT_MS = 15_000;

const PROBE_ARGS = ["-m", "src.broker.probe"];

/** A `detail` is a line, not a log. */
const STDERR_MAX_LEN = 200;

const HINT_INIT = "run: kis-trader init";

/**
 * The probe prints exactly one JSON line, but a dependency that logs to stdout
 * would prepend noise; the contract line is the last one, so parse that.
 * Returns `null` for anything that is not a JSON object.
 */
function parseProbe(stdout: string): Record<string, unknown> | null {
  const lines = stdout.split("\n").filter((l) => l.trim() !== "");
  const last = lines[lines.length - 1];
  if (last === undefined) return null;
  let value: unknown;
  try {
    value = JSON.parse(last);
  } catch {
    return null;
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function clip(text: string): string {
  const flat = text.trim().replace(/\s+/g, " ");
  return flat.length > STDERR_MAX_LEN ? flat.slice(0, STDERR_MAX_LEN) : flat;
}

/** Whole hours, or minutes below the hour — an age is read, not measured. */
function formatAge(ms: number): string {
  const hours = Math.floor(ms / 3_600_000);
  if (hours >= 1) return `${hours}h`;
  return `${Math.max(0, Math.floor(ms / 60_000))}m`;
}

export const defaultProbeRunner: ProbeRunner = (command, args, opts) => {
  // fd 0 is detached: the probe never reads input, and an inherited terminal
  // would let a stray prompt hang `doctor` past its own timeout.
  const res = spawnSync(command, args, {
    cwd: opts.cwd,
    env: opts.env,
    timeout: opts.timeoutMs,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  const stdout = res.stdout ?? "";
  let stderr = res.stderr ?? "";
  if (res.error) {
    // ENOENT or the timeout kill — the message is the only evidence there is.
    stderr += (stderr === "" ? "" : "\n") + res.error.message;
    return { code: -1, stdout, stderr };
  }
  return { code: res.status ?? -1, stdout, stderr };
};

/**
 * Files in `dir` with their mtimes, or `null` when it is not a readable
 * directory. Dotfiles are skipped: a Finder-written `.DS_Store` is not a
 * signal, and counting it would make an empty directory look freshly fed.
 */
export const defaultSignalEntries = (dir: string): SignalEntry[] | null => {
  let names: string[];
  try {
    names = readdirSync(dir, { withFileTypes: true })
      .filter((e) => e.isFile() && !e.name.startsWith("."))
      .map((e) => e.name);
  } catch {
    return null;
  }
  const entries: SignalEntry[] = [];
  for (const name of names) {
    try {
      entries.push({ name, mtimeMs: statSync(join(dir, name)).mtimeMs });
    } catch {
      // Vanished between the listing and the stat — it is not a signal we have.
    }
  }
  return entries;
};

const DEFAULT_DEPS: DoctorDeps = {
  loadConfig: () => defaultLoadConfig(),
  pythonVersion: (binPath) => defaultPythonVersion(binPath),
  isExecutableFile: defaultIsExecutableFile,
  fileExists: (path) => existsSync(path),
  signalEntries: defaultSignalEntries,
  keychainHas: (service, account) => defaultKeychainHas(service, account),
  detectAgents: () => defaultDetectAgents(),
  jobStatus: (job) => defaultJobStatus(job),
  runProbe: defaultProbeRunner,
  now: () => Date.now(),
};

/** A keychain read that threw, turned into a check the user can act on. */
function keychainFailure(err: unknown): { detail: string; hint: string } {
  if (err instanceof KeychainLockedError) {
    return {
      detail: err.message,
      hint: "unlock the login keychain in a Terminal window and re-run",
    };
  }
  return {
    detail: clip(err instanceof Error ? err.message : String(err)),
    hint: HINT_INIT,
  };
}

/**
 * Diagnose the installation.
 *
 * Checks come back in a fixed order, and a config that will not load
 * short-circuits the whole run: every later check reads a field off it, so
 * continuing would only produce a cascade of failures with one real cause.
 */
export function runDoctor(deps: Partial<DoctorDeps> = {}): Check[] {
  const d: DoctorDeps = { ...DEFAULT_DEPS, ...deps };
  const checks: Check[] = [];

  const add = (name: string, status: CheckStatus, detail: string, hint?: string): Check => {
    const check: Check = hint === undefined ? { name, status, detail } : { name, status, detail, hint };
    checks.push(check);
    return check;
  };

  // 1. config — the only check that can end the run.
  const parsed = d.loadConfig();
  if (!parsed.ok) {
    add("config", "fail", parsed.errors.join("; "), HINT_INIT);
    return checks;
  }
  const cfg: Config = parsed.value;
  add("config", "pass", `${cfg.mode} mode, project ${cfg.projectDir}`);

  // 2. state-dir — the root the venv, database and logs hang off. A state dir
  //    inside `projectDir` is exactly the configuration this phase exists to
  //    prevent: `npm i -g` replaces that directory wholesale, destroying the
  //    venv and the trade history with it.
  const insideProject =
    cfg.stateDir === cfg.projectDir || cfg.stateDir.startsWith(cfg.projectDir + "/");
  if (!d.fileExists(cfg.stateDir)) {
    add("state-dir", "fail", `no directory at ${cfg.stateDir}`, HINT_INIT);
  } else if (insideProject) {
    add(
      "state-dir",
      "fail",
      `${cfg.stateDir} is inside the project directory ${cfg.projectDir}`,
      "an npm upgrade replaces the project directory and would destroy the venv and trade history — move the state dir outside it (e.g. ~/.kis-trader) and re-run: kis-trader init",
    );
  } else {
    add("state-dir", "pass", cfg.stateDir);
  }

  // 3. python
  const version = d.pythonVersion(cfg.pythonPath);
  if (isSupported(version) && version) {
    add("python", "pass", `${version[0]}.${version[1]} at ${cfg.pythonPath}`);
  } else if (version) {
    add(
      "python",
      "fail",
      `${version[0]}.${version[1]} at ${cfg.pythonPath} is outside the supported 3.11–3.13 range`,
      "re-run kis-trader init to re-detect Python",
    );
  } else {
    add(
      "python",
      "fail",
      `no interpreter answered at ${cfg.pythonPath}`,
      "re-run kis-trader init to re-detect Python",
    );
  }

  // 4. venv — its outcome also decides whether the KIS probe can run at all.
  const py = venvPython(cfg.stateDir);
  const venvOk = d.isExecutableFile(py);
  if (venvOk) {
    add("venv", "pass", py);
  } else {
    add("venv", "fail", `no interpreter at ${py}`, HINT_INIT);
  }

  // 5. database
  const db = join(cfg.stateDir, "data", "trades.sqlite");
  if (d.fileExists(db)) {
    add("database", "pass", db);
  } else {
    add("database", "fail", `no database at ${db}`, HINT_INIT);
  }

  // 6. keychain-kis — all three items, for the configured mode only.
  try {
    const missing = (["appkey", "secret", "account"] as const)
      .map((kind) => kisAccount(cfg.mode, kind))
      .filter((account) => !d.keychainHas(KIS_SERVICE, account));
    if (missing.length === 0) {
      add("keychain-kis", "pass", `appkey, secret and account stored for ${cfg.mode}`);
    } else {
      add(
        "keychain-kis",
        "fail",
        `missing from the ${KIS_SERVICE} keychain service: ${missing.join(", ")}`,
        HINT_INIT,
      );
    }
  } catch (err) {
    const { detail, hint } = keychainFailure(err);
    add("keychain-kis", "fail", detail, hint);
  }

  // 7. keychain-telegram — optional as a whole, incoherent in halves. Neither
  //    item is a deliberate choice; one item is a half-finished setup that
  //    fails at the first notification.
  try {
    const telegram: [string, boolean][] = [
      [TELEGRAM_TOKEN_ACCOUNT, d.keychainHas(TELEGRAM_SERVICE, TELEGRAM_TOKEN_ACCOUNT)],
      [TELEGRAM_CHATID_ACCOUNT, d.keychainHas(TELEGRAM_SERVICE, TELEGRAM_CHATID_ACCOUNT)],
    ];
    const missing = telegram.filter(([, has]) => !has).map(([account]) => account);
    if (missing.length === 0) {
      add("keychain-telegram", "pass", "bot token and chat id stored");
    } else if (missing.length === telegram.length) {
      add(
        "keychain-telegram",
        "warn",
        "not configured — Telegram notifications are off",
        "optional: run kis-trader init to add the bot token and chat id",
      );
    } else {
      add(
        "keychain-telegram",
        "fail",
        `half configured — missing from the ${TELEGRAM_SERVICE} keychain service: ${missing.join(", ")}`,
        HINT_INIT,
      );
    }
  } catch (err) {
    const { detail, hint } = keychainFailure(err);
    add("keychain-telegram", "fail", detail, hint);
  }

  // 8. keychain-krx — the signal producer's KRX login. Optional as a pair: KRX
  //    serves the same listings unauthenticated, so absence degrades the
  //    producer rather than breaking it (D4). One half is neither state — it is
  //    a setup that was interrupted, and it fails at the first authenticated
  //    fetch, so it is reported now.
  try {
    const krx: [string, boolean][] = [
      [KRX_ID_ACCOUNT, d.keychainHas(SIGNAL_SERVICE, KRX_ID_ACCOUNT)],
      [KRX_PW_ACCOUNT, d.keychainHas(SIGNAL_SERVICE, KRX_PW_ACCOUNT)],
    ];
    const missing = krx.filter(([, has]) => !has).map(([account]) => account);
    if (missing.length === 0) {
      add("keychain-krx", "pass", "KRX id and password stored for the signal producer");
    } else if (missing.length === krx.length) {
      add(
        "keychain-krx",
        "warn",
        "not configured — the signal bot falls back to unauthenticated KRX access",
        "optional: run kis-trader init to store the KRX id and password",
      );
    } else {
      add(
        "keychain-krx",
        "fail",
        `half configured — missing from the ${SIGNAL_SERVICE} keychain service: ${missing.join(", ")}`,
        HINT_INIT,
      );
    }
  } catch (err) {
    // A locked keychain is not an absent credential: it says nothing about what
    // is stored, so it cannot be softened to the optional-is-fine warn above.
    const { detail, hint } = keychainFailure(err);
    add("keychain-krx", "fail", detail, hint);
  }

  // 9. keychain-brave — a single optional item, so absent is simply off (D4).
  try {
    if (d.keychainHas(SIGNAL_SERVICE, BRAVE_KEY_ACCOUNT)) {
      add("keychain-brave", "pass", "Brave Search API key stored");
    } else {
      add(
        "keychain-brave",
        "warn",
        "not configured — news enrichment is disabled",
        "optional: run kis-trader init to add the Brave Search API key",
      );
    }
  } catch (err) {
    const { detail, hint } = keychainFailure(err);
    add("keychain-brave", "fail", detail, hint);
  }

  // 10. llm-agent
  const agents = d.detectAgents();
  const configured = agents[cfg.llmAgent];
  const found = (Object.keys(agents) as SupportedCli[]).filter((k) => agents[k] !== null);
  if (configured) {
    const version = configured.version === null ? "" : ` (${configured.version})`;
    add("llm-agent", "pass", `${cfg.llmAgent} at ${configured.path}${version}`);
  } else if (found.length > 0) {
    add(
      "llm-agent",
      "warn",
      `${cfg.llmAgent} is configured but not installed; found: ${found.join(", ")}`,
      `re-run kis-trader init and pick one of: ${found.join(", ")}`,
    );
  } else {
    add(
      "llm-agent",
      "fail",
      "no supported LLM CLI found (claude, codex, pi, gemini)",
      "install one of claude, codex, pi or gemini, then re-run kis-trader init",
    );
  }

  // 11. news-backend — the producer's news enrichment. Its binary is resolved
  //     from the same detection pass as `llm-agent`: they are independent
  //     settings, so the configured backend is looked up on its own. "none" is
  //     a deliberate choice, not a gap; anything else that is not installed
  //     produces no news signal at all and never says so at runtime.
  if (cfg.newsLlmBackend === "none") {
    add("news-backend", "pass", "disabled (no LLM cost)");
  } else {
    const backend = agents[cfg.newsLlmBackend];
    if (backend) {
      const backendVersion = backend.version === null ? "" : ` (${backend.version})`;
      add("news-backend", "pass", `${cfg.newsLlmBackend} at ${backend.path}${backendVersion}`);
    } else {
      add(
        "news-backend",
        "fail",
        `${cfg.newsLlmBackend} is configured for news enrichment but not installed`,
        `install ${cfg.newsLlmBackend}, or set newsLlmBackend to "none" to disable news enrichment`,
      );
    }
  }

  // 12. signal-dir — existence *and* freshness (D12). A directory that exists
  //     but stopped being written to looks healthy to `existsSync` and is not.
  //     The producer ships in this package, so every hint names the command
  //     that writes a file on demand rather than an external project.
  const entries = d.signalEntries(cfg.signalDir);
  if (entries === null) {
    add(
      "signal-dir",
      "fail",
      `no directory at ${cfg.signalDir}`,
      `create ${cfg.signalDir}, then run: kis-trader start signalKr`,
    );
  } else if (entries.length === 0) {
    add(
      "signal-dir",
      "warn",
      `${cfg.signalDir} exists but is empty`,
      `no signals yet — run: kis-trader start signalKr to write one into ${cfg.signalDir}`,
    );
  } else {
    const newest = entries.reduce((a, b) => (b.mtimeMs > a.mtimeMs ? b : a));
    const age = d.now() - newest.mtimeMs;
    const detail = `${entries.length} file(s), newest ${newest.name} ${formatAge(age)} old`;
    if (age < STALE_SIGNAL_MS) {
      add("signal-dir", "pass", detail);
    } else {
      add(
        "signal-dir",
        "warn",
        detail,
        `signals are stale — run: kis-trader start signalKr to refresh ${cfg.signalDir}`,
      );
    }
  }

  // 13. kis-api — a real round trip, run inside the venv so the credentials stay
  //    in the Python process (D18). Nothing is spawned without an interpreter
  //    to spawn: a missing venv is already reported above.
  if (!venvOk) {
    add("kis-api", "warn", "skipped — venv missing", HINT_INIT);
  } else {
    const retry = `re-run \`${py} -m src.broker.probe\` in ${cfg.projectDir} to see the full error`;
    const out = d.runProbe(py, PROBE_ARGS, {
      cwd: cfg.projectDir,
      env: { ...process.env, KIS_MODE: cfg.mode },
      timeoutMs: PROBE_TIMEOUT_MS,
    });
    const result = parseProbe(out.stdout);
    // The probe's own `detail` is the readable half of the contract; stderr is
    // where a crash that never reached the contract leaves its trace.
    const probeDetail = result === null ? "" : clip(str(result.detail));
    const stderrDetail = clip(out.stderr);

    if (result === null) {
      add(
        "kis-api",
        "fail",
        stderrDetail !== "" ? stderrDetail : `probe produced no JSON (exit ${out.code})`,
        retry,
      );
    } else if (result.ok === true) {
      const mode = str(result.mode) !== "" ? str(result.mode) : cfg.mode;
      const masked = str(result.cano_masked);
      add(
        "kis-api",
        "pass",
        masked !== "" ? `${mode} account reachable (${masked})` : `${mode} account reachable`,
      );
    } else {
      const reason = str(result.reason);
      switch (reason) {
        case "missing_credentials":
          add(
            "kis-api",
            "fail",
            probeDetail !== "" ? probeDetail : "the probe found no stored KIS credentials",
            HINT_INIT,
          );
          break;
        case "auth_failed":
          add(
            "kis-api",
            "fail",
            probeDetail !== "" ? probeDetail : "KIS rejected the stored app key/secret",
            "app key/secret rejected — re-run kis-trader init",
          );
          break;
        case "rate_limited":
          // Throttling says nothing about the credentials, so it must never be
          // reported as a credential problem.
          add(
            "kis-api",
            "warn",
            `the probe was throttled by KIS, not rejected — credentials were not tested${
              probeDetail === "" ? "" : `: ${probeDetail}`
            }`,
            "retry in a minute — KIS caps requests per second",
          );
          break;
        case "network":
          add(
            "kis-api",
            "warn",
            probeDetail !== "" ? probeDetail : "the probe could not reach KIS",
            "check connectivity to openapivts.koreainvestment.com",
          );
          break;
        default: {
          const fallback =
            probeDetail !== ""
              ? probeDetail
              : `probe reported reason "${reason}" (exit ${out.code})`;
          add("kis-api", "fail", stderrDetail !== "" ? stderrDetail : fallback, retry);
        }
      }
    }
  }

  // 14. jobs — one per key in the config schema, which is the job inventory
  //     (WIKI infrastructure-config-environment-config rule 4), so the two
  //     signal jobs are reported by the same loop, from `launchctl` itself
  //     (WIKI platforms-processes-background-services rule 4) rather than from
  //     an install command's exit code. `absent` means something different
  //     depending on whether the user asked for the job.
  for (const key of JOB_KEYS as readonly JobName[]) {
    const label = labelFor(key);
    const status = d.jobStatus(key);
    if (status === "loaded") {
      add(`job:${key}`, "pass", `${label} is loaded`);
    } else if (status === "installed-not-loaded") {
      add(
        `job:${key}`,
        "warn",
        `${label} is installed but not loaded`,
        `launchctl bootstrap gui/$UID ${plistPath(label)}`,
      );
    } else if (cfg.jobs[key]) {
      add(
        `job:${key}`,
        "fail",
        `${label} is enabled in the config but not installed`,
        HINT_INIT,
      );
    } else {
      add(
        `job:${key}`,
        "warn",
        `${label} is not installed (jobs.${key} is false)`,
        `set jobs.${key} to true and re-run kis-trader init to install it`,
      );
    }
  }

  return checks;
}

const GLYPH: Record<CheckStatus, string> = { pass: "✔", warn: "!", fail: "✖" };

/**
 * One aligned line per check, with a non-pass check's hint indented underneath
 * it — the remedy belongs next to the problem, not in a legend at the bottom.
 */
export function formatChecks(checks: Check[]): string {
  const width = Math.max(0, ...checks.map((c) => c.name.length));
  const lines: string[] = [];
  for (const c of checks) {
    lines.push(`${GLYPH[c.status]} ${c.name.padEnd(width)}  ${c.detail}`.trimEnd());
    if (c.hint !== undefined && c.hint !== "") {
      lines.push(`${" ".repeat(width + 3)}↳ ${c.hint}`);
    }
  }
  return lines.join("\n");
}

/** `1` when anything failed. A warn is a note, never a broken command. */
export function exitCodeFor(checks: Check[]): number {
  return checks.some((c) => c.status === "fail") ? 1 : 0;
}
