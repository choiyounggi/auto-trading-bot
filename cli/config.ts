/**
 * kis-trader configuration: schema, validation, load, save.
 *
 * The parser is the single source of truth — `Config` is the type it produces,
 * so validation and typing cannot drift apart. Every value here crosses a
 * trust boundary (a JSON file a user can hand-edit), so nothing is cast; the
 * whole object is validated once and the caller crashes on failure rather than
 * discovering a missing key hours later inside a launchd job.
 *
 * Required keys carry no default: a default that suits a dev box turns a
 * missing production value into a silent misconfiguration.
 */

import { mkdirSync, readFileSync, writeFileSync, chmodSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export const MODES = ["paper", "real"] as const;
export type Mode = (typeof MODES)[number];

export const AGENTS = ["claude", "codex", "pi", "gemini"] as const;
export type Agent = (typeof AGENTS)[number];

export const JOB_KEYS = ["orchestrator", "monitor", "reconciler", "dipBuy"] as const;
export type JobName = (typeof JOB_KEYS)[number];

/** Keys the user must supply. No defaults — see the module docstring. */
export const REQUIRED = ["mode", "projectDir", "pythonPath", "signalDir"] as const;

/** Absolute-path keys, checked after the required-presence pass. */
const PATH_KEYS = ["projectDir", "pythonPath", "signalDir"] as const;

export interface Config {
  mode: Mode;
  projectDir: string;
  pythonPath: string;
  signalDir: string;
  llmAgent: Agent;
  jobs: Record<JobName, boolean>;
}

export type ParseResult =
  | { ok: true; value: Config }
  | { ok: false; errors: string[] };

export const CONFIG_FILENAME = "config.json";

/**
 * Resolve the state directory. `KIS_TRADER_HOME` wins when set, but only when
 * absolute — a relative value would resolve against launchd's working
 * directory and silently point somewhere nobody intended.
 */
export function configHome(env: NodeJS.ProcessEnv = process.env): string {
  const override = env.KIS_TRADER_HOME;
  if (override !== undefined && override.trim() !== "") {
    if (!override.startsWith("/")) {
      throw new Error("KIS_TRADER_HOME must be an absolute path");
    }
    return override;
  }
  return join(homedir(), ".kis-trader");
}

export function configPath(home: string = configHome()): string {
  return join(home, CONFIG_FILENAME);
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * Validate an untrusted value into a `Config`.
 *
 * Every problem is collected — a user fixing a hand-edited file should see the
 * whole list, not one error per run.
 */
export function parseConfig(input: unknown): ParseResult {
  if (!isPlainObject(input)) {
    return { ok: false, errors: ["config must be a JSON object"] };
  }
  const errors: string[] = [];

  // Presence first. An empty string is "missing", not "malformed" — reporting
  // both for one blank field would just be noise.
  const present = new Set<string>();
  for (const key of REQUIRED) {
    const v = input[key];
    if (typeof v !== "string" || v.trim() === "") {
      errors.push(`${key} is required`);
    } else {
      present.add(key);
    }
  }

  if (present.has("mode") && !MODES.includes(input.mode as Mode)) {
    errors.push('mode must be "paper" or "real"');
  }

  for (const key of PATH_KEYS) {
    if (present.has(key) && !(input[key] as string).startsWith("/")) {
      errors.push(`${key} must be an absolute path`);
    }
  }

  let llmAgent: Agent = "claude";
  if (input.llmAgent !== undefined) {
    if (typeof input.llmAgent !== "string" || !AGENTS.includes(input.llmAgent as Agent)) {
      errors.push(`llmAgent must be one of ${AGENTS.join(", ")}`);
    } else {
      llmAgent = input.llmAgent as Agent;
    }
  }

  const jobs: Record<JobName, boolean> = {
    orchestrator: true,
    monitor: true,
    reconciler: true,
    dipBuy: true,
  };
  if (input.jobs !== undefined) {
    if (!isPlainObject(input.jobs)) {
      errors.push("jobs must be an object");
    } else {
      for (const name of JOB_KEYS) {
        const v = input.jobs[name];
        if (v === undefined) continue;
        if (typeof v !== "boolean") {
          errors.push(`jobs.${name} must be a boolean`);
        } else {
          jobs[name] = v;
        }
      }
    }
  }

  if (errors.length > 0) return { ok: false, errors };

  return {
    ok: true,
    value: {
      mode: input.mode as Mode,
      projectDir: input.projectDir as string,
      pythonPath: input.pythonPath as string,
      signalDir: input.signalDir as string,
      llmAgent,
      jobs,
    },
  };
}

/** Read and validate `<home>/config.json`. */
export function loadConfig(home: string = configHome()): ParseResult {
  const path = configPath(home);
  let raw: string;
  try {
    raw = readFileSync(path, "utf8");
  } catch {
    return {
      ok: false,
      errors: [`no config at ${path} — run \`kis-trader init\` first`],
    };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ok: false, errors: [`${path} is not valid JSON`] };
  }
  return parseConfig(parsed);
}

/** Write the config at mode 0600 and return the path written. */
export function saveConfig(cfg: Config, home: string = configHome()): string {
  mkdirSync(home, { recursive: true });
  const path = configPath(home);
  writeFileSync(path, JSON.stringify(cfg, null, 2) + "\n", { mode: 0o600 });
  // writeFileSync's mode only applies at creation; chmod covers the overwrite
  // case where the file already existed with looser permissions.
  chmodSync(path, 0o600);
  return path;
}
