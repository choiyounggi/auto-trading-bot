/**
 * `kis-trader init` — the eight-step onboarding flow.
 *
 * This module only sequences and reports; every effect (keychain write, config
 * write, venv build, launchd load) belongs to a module that already owns it and
 * arrives through `InitDeps`. That is what makes the whole flow drivable from a
 * test without touching the developer's machine — an `init` that could only be
 * exercised for real would be exercised by installing launchd jobs on whoever
 * ran the suite.
 *
 * Two rules shape the ordering:
 *
 * - **Secrets never touch `config.json`.** The KIS key/secret/account, the
 *   Telegram token/chat id, and the KRX login and Brave API key collected for
 *   the signal pipeline go to the login keychain and nowhere else; nothing
 *   captured by a secret prompt is ever written back to the screen, because a
 *   printed credential is a leaked credential the moment the terminal is
 *   scrolled, recorded, or piped into a log.
 * - **Required values are asked for, never guessed.** `mode`, `projectDir`,
 *   `pythonPath` and `signalDir` carry no defaults in the schema, so the flow
 *   collects each one and hands the result to `parseConfig` before saving. An
 *   invalid config is reported and dropped rather than written, since a file
 *   that parses only halfway turns a setup mistake into a launchd job that
 *   fails hours later with no operator watching.
 *
 * Anything fatal returns 1 *before* the next side effect, so a run that stops
 * never leaves a half-written keychain or a config the engine cannot load.
 */

import { statSync } from "node:fs";
import { join } from "node:path";

import {
  AGENTS,
  JOB_KEYS,
  MODES,
  NEWS_BACKENDS,
  defaultSignalDir,
  parseConfig,
  saveConfig as saveConfigReal,
  type Agent,
  type Config,
  type Mode,
  type NewsBackend,
} from "./config.js";
import {
  BRAVE_KEY_ACCOUNT,
  KIS_SERVICE,
  KRX_ID_ACCOUNT,
  KRX_PW_ACCOUNT,
  SIGNAL_SERVICE,
  TELEGRAM_CHATID_ACCOUNT,
  TELEGRAM_SERVICE,
  TELEGRAM_TOKEN_ACCOUNT,
  kisAccount,
  keychainSet as keychainSetReal,
} from "./keychain.js";
import {
  detectAgents as detectAgentsReal,
  type DetectedAgent,
  type SupportedCli,
} from "./resolve-cli-path.js";
import { findPython as findPythonReal } from "./python.js";
import {
  JOBS,
  installJob as installJobReal,
  type InstallResult,
  type JobKey,
} from "./launchd.js";
import {
  bootstrapPython as bootstrapPythonReal,
  type BootstrapOptions,
  type StepResult,
} from "./bootstrap.js";
import {
  askDefault,
  askValidated,
  choose,
  promptSecret,
  validators,
  yesNo,
  type Io,
} from "./prompt.js";

const TOTAL_STEPS = 8;

/** Matches `prompt.ts`, so a re-prompting loop here behaves like one there. */
const MAX_PROMPT_ATTEMPTS = 3;

/**
 * Every collaborator that reaches outside this process.
 *
 * The real implementations are the default; a test replaces them wholesale.
 */
export interface InitDeps {
  detectAgents: () => Record<SupportedCli, DetectedAgent | null>;
  findPython: () => string | null;
  keychainSet: (service: string, account: string, secret: string) => void;
  saveConfig: (cfg: Config, home: string) => string;
  bootstrapPython: (cfg: Config, opts: BootstrapOptions) => StepResult[];
  installJob: (job: JobKey, cfg: Config, home: string) => InstallResult;
  /** True only when `path` is an existing *directory*. */
  dirExists: (path: string) => boolean;
}

const defaultDeps: InitDeps = {
  detectAgents: () => detectAgentsReal(),
  findPython: () => findPythonReal(),
  keychainSet: (service, account, secret) => keychainSetReal(service, account, secret),
  saveConfig: (cfg, home) => saveConfigReal(cfg, home),
  bootstrapPython: (cfg, opts) => bootstrapPythonReal(cfg, opts),
  installJob: (job, cfg, home) => installJobReal(job, cfg, home),
  dirExists: (path) => {
    try {
      return statSync(path).isDirectory();
    } catch {
      return false;
    }
  },
};

/** A `[service, account, value]` triple bound for the login keychain. */
type KeychainItem = readonly [service: string, account: string, value: string];

function writers(out: NodeJS.WritableStream) {
  const line = (text: string): void => {
    out.write(text + "\n");
  };
  return {
    line,
    section: (n: number, title: string) =>
      line(`\n── Step ${n}/${TOTAL_STEPS} — ${title} ──`),
    ok: (m: string) => line(`  ✓ ${m}`),
    warn: (m: string) => line(`  ! ${m}`),
    fail: (m: string) => line(`  ✗ ${m}`),
  };
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** Human-readable form of a job's schedule, for the install question. */
function scheduleSummary(job: JobKey): string {
  const schedule = JOBS[job].schedule;
  if ("intervalSec" in schedule) return `every ${schedule.intervalSec}s`;
  // A job may run at several fixed times per weekday (`signalUs`: 22:35 and
  // 23:35). Listing every one matters here — this string is what the user reads
  // before agreeing to install the job.
  const times = "times" in schedule ? schedule.times : [schedule];
  return `daily ${times.map((t) => `${pad2(t.hour)}:${pad2(t.minute)}`).join(", ")}`;
}

/**
 * Write every item, stopping at the first failure and returning its message.
 *
 * The message is safe to print: `keychain.ts` redacts the secret out of the
 * failures that could echo it back, and reports a locked keychain as its own
 * error whose text tells the user to unlock. The caller aborts on a non-null
 * result rather than continuing with a partially populated keychain, because
 * the engine reading three of five items fails at order time instead of setup
 * time.
 */
function store(deps: InitDeps, items: readonly KeychainItem[]): string | null {
  for (const [service, account, value] of items) {
    try {
      deps.keychainSet(service, account, value);
    } catch (err) {
      return err instanceof Error ? err.message : String(err);
    }
  }
  return null;
}

/**
 * Read a secret until `validate` accepts it.
 *
 * `prompt.ts` has no validating counterpart to `promptSecret`, and the value
 * must not be echoed while it is being corrected, so the loop lives here.
 * Exhaustion throws exactly like `askValidated`: unlike a path or a menu
 * choice, a credential has no safe fallback to substitute.
 */
async function promptSecretValidated(
  q: string,
  validate: (s: string) => string | null,
  io: Io,
  attempts = MAX_PROMPT_ATTEMPTS,
): Promise<string> {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const value = (await promptSecret(q, io)).trim();
    const problem = validate(value);
    if (problem === null) return value;
    io.output.write(problem + "\n");
  }
  throw new Error("could not read a valid value for: " + q);
}

/**
 * Ask with a default and re-prompt until `validate` accepts the answer.
 *
 * After the last attempt the (known-good) default wins instead of throwing —
 * the same choice `choose` makes, so a mistyped path cannot wedge a run.
 */
async function askDefaultValidated(
  q: string,
  fallback: string,
  validate: (s: string) => string | null,
  io: Io,
  attempts = MAX_PROMPT_ATTEMPTS,
): Promise<string> {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const value = await askDefault(q, fallback, io);
    const problem = validate(value);
    if (problem === null) return value;
    io.output.write(problem + "\n");
  }
  return fallback;
}

/**
 * Walk the eight onboarding steps.
 *
 * Returns a process exit code: 0 when setup completed, 1 when the user aborted
 * or a step failed.
 */
export async function runInit(opts: {
  home: string;
  projectDir: string;
  io?: Io;
  deps?: Partial<InitDeps>;
}): Promise<number> {
  const io: Io = opts.io ?? { input: process.stdin, output: process.stdout };
  const deps: InitDeps = { ...defaultDeps, ...opts.deps };
  const { line, section, ok, warn, fail } = writers(io.output);

  // ── 1/8 ─ trading mode ──────────────────────────────────────────────
  section(1, "Trading mode");
  const mode = await choose<Mode>("Trading mode", MODES, "paper", io);
  if (mode === "real") {
    // Defaults to no: an empty answer at this prompt must never arm live
    // orders, so the safe outcome is the one that requires no keystroke.
    const confirmed = await yesNo(
      "REAL money mode. Orders will use your live account. Continue?",
      false,
      io,
    );
    if (!confirmed) {
      fail("aborted — nothing was written.");
      return 1;
    }
  }
  ok(`mode: ${mode}`);

  // ── 2/8 ─ KIS credentials ───────────────────────────────────────────
  section(2, "KIS credentials");
  const appKey = await promptSecret("KIS app key: ", io);
  const appSecret = await promptSecret("KIS app secret: ", io);
  const accountNo = await askValidated(
    "KIS account number (10 digits): ",
    validators.kisAccount10,
    io,
  );
  const kisProblem = store(deps, [
    [KIS_SERVICE, kisAccount(mode, "appkey"), appKey],
    [KIS_SERVICE, kisAccount(mode, "secret"), appSecret],
    [KIS_SERVICE, kisAccount(mode, "account"), accountNo],
  ]);
  if (kisProblem !== null) {
    fail(kisProblem);
    return 1;
  }
  ok(`KIS credentials stored in the login keychain (${mode})`);

  // ── 3/8 ─ Telegram ──────────────────────────────────────────────────
  section(3, "Telegram");
  let telegram: KeychainItem[] = [];
  if (await yesNo("Configure Telegram notifications?", true, io)) {
    try {
      const token = await promptSecretValidated(
        "Telegram bot token: ",
        validators.telegramToken,
        io,
      );
      const chatId = await askValidated(
        "Telegram chat id: ",
        validators.telegramChatId,
        io,
      );
      telegram = [
        [TELEGRAM_SERVICE, TELEGRAM_TOKEN_ACCOUNT, token],
        [TELEGRAM_SERVICE, TELEGRAM_CHATID_ACCOUNT, chatId],
      ];
    } catch {
      // The step is optional by design, so an unusable value degrades to the
      // exact state answering "no" produces: no keychain item, no config key.
      // Nothing has been written at this point, so there is nothing to undo.
      warn("no valid Telegram value — skipping notifications.");
    }
  } else {
    ok("skipped — notifications stay off");
  }
  if (telegram.length > 0) {
    const problem = store(deps, telegram);
    if (problem !== null) {
      fail(problem);
      return 1;
    }
    ok("Telegram credentials stored in the login keychain");
  }

  // ── 4/8 ─ LLM CLI ───────────────────────────────────────────────────
  section(4, "LLM CLI");
  const detected = deps.detectAgents();
  const found: Agent[] = [];
  for (const name of AGENTS) {
    const agent = detected[name];
    if (!agent) {
      warn(`${name}: not installed`);
      continue;
    }
    ok(`${name}: ${agent.path}${agent.version === null ? "" : ` (${agent.version})`}`);
    found.push(name);
  }
  if (found.length === 0) {
    fail(`No supported LLM CLI found. Install one of: ${AGENTS.join(", ")} and re-run.`);
    return 1;
  }
  const llmAgent = await choose<Agent>("Default agent", found, found[0], io);

  // ── 5/8 ─ Python + signal directory ─────────────────────────────────
  section(5, "Python + signal directory");
  const pythonPath = deps.findPython();
  if (pythonPath === null) {
    fail("No supported Python interpreter found.");
    line("Install Python 3.11–3.13 (e.g. brew install python@3.12) and re-run.");
    return 1;
  }
  ok(`python: ${pythonPath}`);
  const signalDir = await askDefaultValidated(
    "Signal directory (signal pipeline output)",
    defaultSignalDir(opts.projectDir),
    validators.absolutePath,
    io,
  );
  if (deps.dirExists(signalDir)) {
    ok(`signals: ${signalDir}`);
  } else {
    // Now the normal case on a fresh install: the producer lives in this package
    // and creates the directory on its first run. Blocking here would make
    // `init` impossible to finish before that run, so this warns and moves on.
    warn(`${signalDir} does not exist yet — no signals will be read until it does.`);
  }

  // ── 6/8 ─ signal pipeline ───────────────────────────────────────────
  //
  // Both credentials are optional (the pipeline falls back to public data and
  // to no news enrichment), so "no" is a real path that writes nothing at all —
  // an empty keychain item would read back as *configured* on the Python side
  // and fail later as an authentication error instead of a missing credential.
  section(6, "신호 파이프라인");
  const signalItems: KeychainItem[] = [];
  if (await yesNo("KRX 로그인을 설정할까요? (수급 데이터 정확도가 올라갑니다)", true, io)) {
    try {
      const krxId = await askValidated("KRX ID: ", validators.nonEmpty, io);
      const krxPw = await promptSecretValidated("KRX 비밀번호: ", validators.nonEmpty, io);
      // Pushed only once *both* halves are in hand: an id stored without its
      // password is a login the engine cannot use, and it looks configured.
      signalItems.push(
        [SIGNAL_SERVICE, KRX_ID_ACCOUNT, krxId],
        [SIGNAL_SERVICE, KRX_PW_ACCOUNT, krxPw],
      );
    } catch {
      // Optional by design, so an unusable value degrades to exactly the state
      // answering "no" produces. Nothing has been written yet.
      warn("KRX 값을 읽지 못했습니다 — 공개 데이터만 사용합니다.");
    }
  } else {
    ok("KRX: 건너뜀 — 공개 데이터만 사용합니다");
  }
  // Defaults to no: this one costs the user an API signup, and the pipeline
  // works without it.
  if (await yesNo("Brave Search API 키를 설정할까요? (뉴스 보강용, 선택)", false, io)) {
    try {
      const braveKey = await promptSecretValidated(
        "Brave Search API key: ",
        validators.nonEmpty,
        io,
      );
      signalItems.push([SIGNAL_SERVICE, BRAVE_KEY_ACCOUNT, braveKey]);
    } catch {
      warn("Brave Search 키를 읽지 못했습니다 — 뉴스 보강 없이 진행합니다.");
    }
  } else {
    ok("Brave Search: 건너뜀 — 뉴스 보강 없이 동작합니다");
  }
  if (signalItems.length > 0) {
    const problem = store(deps, signalItems);
    if (problem !== null) {
      fail(problem);
      return 1;
    }
    ok("신호 자격증명을 로그인 키체인에 저장했습니다");
  }
  const newsLlmBackend = await choose<NewsBackend>(
    "뉴스 분류 LLM",
    NEWS_BACKENDS,
    "none",
    io,
  );
  ok(`뉴스 분류 LLM: ${newsLlmBackend}`);

  // ── 7/8 ─ bootstrap ─────────────────────────────────────────────────
  section(7, "Bootstrap");
  const parsed = parseConfig({
    mode,
    projectDir: opts.projectDir,
    pythonPath,
    signalDir,
    llmAgent,
    newsLlmBackend,
  });
  if (!parsed.ok) {
    fail("config is invalid — nothing was saved:");
    for (const problem of parsed.errors) line(`    - ${problem}`);
    return 1;
  }
  const cfg = parsed.value;
  const configFile = deps.saveConfig(cfg, opts.home);
  ok(`config saved: ${configFile}`);

  const steps = deps.bootstrapPython(cfg, {
    onStep: (r) => (r.ok ? ok(`${r.step}: ${r.detail}`) : fail(`${r.step}: ${r.detail}`)),
  });
  // `onStep` already printed the failing step's detail; stopping here keeps the
  // launchd jobs from being installed against a venv that does not work.
  if (steps.some((step) => !step.ok)) return 1;

  // ── 8/8 ─ launchd ───────────────────────────────────────────────────
  section(8, "launchd jobs");
  for (const job of JOB_KEYS) {
    const wanted = await yesNo(`Install ${job} (${scheduleSummary(job)})?`, true, io);
    cfg.jobs[job] = wanted;
    if (!wanted) {
      ok(`${job}: skipped`);
      continue;
    }
    const result = deps.installJob(job, cfg, opts.home);
    if (result.loaded) {
      ok(result.message);
    } else {
      // Reported, never fatal: one job that will not load must not cost the
      // user the other four.
      warn(result.message);
      line(`    run later: launchctl bootstrap gui/$UID ${result.path}`);
    }
  }
  // The answers are part of the config, so they are saved even when a job
  // failed to load — `doctor` compares the file against what launchd has.
  deps.saveConfig(cfg, opts.home);

  line("");
  line("Setup complete.");
  line(`  config: ${configFile}`);
  line(`  logs:   ${join(opts.home, "logs")}`);
  line("");
  line("Next:");
  line("  kis-trader doctor");
  line("  kis-trader logs");
  return 0;
}
