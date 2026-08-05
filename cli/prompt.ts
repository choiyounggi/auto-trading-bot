/**
 * Interactive prompt helpers for the onboarding flow.
 *
 * Every function takes an injectable `Io` instead of reaching for
 * `process.stdin`/`process.stdout` directly, so the whole module is drivable
 * from tests with ordinary streams.
 *
 * `promptSecret` is the reason this file exists. A secret's exposure surface
 * has to be kept as small as possible, and a terminal prompt has two of them:
 * the echoed characters on screen and whatever the shell records. Raw mode
 * removes the echo — but only on a real TTY. When stdin is a pipe there is no
 * echo to suppress, so the input *will* be visible; the fallback says so out
 * loud rather than reading the value under a prompt that implies it was
 * hidden. Silently pretending to hide a secret is how a secret ends up in a
 * log or a screen recording nobody thought to check.
 */

import { createInterface } from "node:readline/promises";
import { StringDecoder } from "node:string_decoder";

export interface Io {
  input: NodeJS.ReadableStream & {
    isTTY?: boolean;
    setRawMode?: (b: boolean) => void;
  };
  output: NodeJS.WritableStream;
}

/** Warning shown before a secret is read from a stream that cannot hide it. */
const NOT_A_TTY_WARNING =
  "! stdin is not a TTY — input will be visible on screen";

function defaultIo(): Io {
  return { input: process.stdin, output: process.stdout };
}

/**
 * Run `fn` against a single readline interface.
 *
 * One interface per call, not per question: readline consumes whole chunks, so
 * closing and reopening between the attempts of a re-prompting loop would drop
 * any line that arrived early.
 */
async function withRl<T>(
  io: Io,
  fn: (question: (q: string) => Promise<string>) => Promise<T>,
): Promise<T> {
  const rl = createInterface({ input: io.input, output: io.output });
  try {
    return await fn(
      (q) =>
        // A closed stdin never delivers a line, so the raw `rl.question`
        // promise would hang forever instead of failing.
        new Promise<string>((resolve, reject) => {
          const onClose = () =>
            reject(new Error("input closed before an answer was given"));
          rl.once("close", onClose);
          rl.question(q).then(
            (answer) => {
              rl.off("close", onClose);
              resolve(answer);
            },
            (err) => {
              rl.off("close", onClose);
              reject(err);
            },
          );
        }),
    );
  } finally {
    rl.close();
  }
}

/** Ask `q` and return the trimmed answer. */
export async function ask(q: string, io: Io = defaultIo()): Promise<string> {
  return withRl(io, async (question) => (await question(q)).trim());
}

/** Ask `q`, showing `fallback`; an empty (or blank) answer returns `fallback`. */
export async function askDefault(
  q: string,
  fallback: string,
  io: Io = defaultIo(),
): Promise<string> {
  return withRl(io, async (question) => {
    const answer = (await question(`${q} [${fallback}]: `)).trim();
    return answer === "" ? fallback : answer;
  });
}

/** Ask a yes/no question. An empty answer takes `defaultYes`. */
export async function yesNo(
  q: string,
  defaultYes: boolean,
  io: Io = defaultIo(),
): Promise<boolean> {
  return withRl(io, async (question) => {
    const hint = defaultYes ? "[Y/n]" : "[y/N]";
    const answer = (await question(`${q} ${hint}: `)).trim();
    if (answer === "") return defaultYes;
    return answer.toLowerCase().startsWith("y");
  });
}

/** How many times a re-prompting helper reads before giving up. */
const MAX_ATTEMPTS = 3;

/**
 * Pick one of `options`. An empty answer takes `fallback`; an unlisted answer
 * re-prompts, and after `MAX_ATTEMPTS` unlisted answers `fallback` wins rather
 * than blocking an unattended run forever.
 */
export async function choose<T extends string>(
  q: string,
  options: readonly T[],
  fallback: T,
  io: Io = defaultIo(),
): Promise<T> {
  if (options.length === 0) {
    throw new Error("choose() needs at least one option");
  }
  const prompt = `${q} [${fallback}] (${options.join("/")}): `;
  return withRl(io, async (question) => {
    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      const answer = (await question(prompt)).trim();
      if (answer === "") return fallback;
      if ((options as readonly string[]).includes(answer)) return answer as T;
    }
    return fallback;
  });
}

/**
 * Ask until `validate` accepts the answer. `validate` returns an error message
 * to show, or `null` when the value is good. The answer is trimmed before
 * validation, so a validator never has to defend against stray whitespace.
 */
export async function askValidated(
  q: string,
  validate: (s: string) => string | null,
  io: Io = defaultIo(),
  attempts = MAX_ATTEMPTS,
): Promise<string> {
  return withRl(io, async (question) => {
    for (let attempt = 0; attempt < attempts; attempt++) {
      const answer = (await question(q)).trim();
      const problem = validate(answer);
      if (problem === null) return answer;
      io.output.write(problem + "\n");
    }
    throw new Error("could not read a valid value for: " + q);
  });
}

/**
 * Read a secret. On a TTY the characters are consumed in raw mode and never
 * echoed; anywhere else the caller is warned first — see the module docstring.
 */
export async function promptSecret(
  q: string,
  io: Io = defaultIo(),
): Promise<string> {
  const { input } = io;
  if (input.isTTY !== true || typeof input.setRawMode !== "function") {
    io.output.write(NOT_A_TTY_WARNING + "\n");
    return ask(q, io);
  }
  const setRawMode = input.setRawMode.bind(input);
  const wasRaw = (input as { isRaw?: boolean }).isRaw === true;

  io.output.write(q);
  setRawMode(true);
  try {
    return await new Promise<string>((resolve, reject) => {
      const decoder = new StringDecoder("utf8");
      let value = "";

      const settle = (finish: () => void) => {
        input.off("data", onData);
        input.off("end", onEnd);
        input.pause();
        finish();
      };

      const onData = (chunk: Buffer | string) => {
        const text = typeof chunk === "string" ? chunk : decoder.write(chunk);
        for (const ch of text) {
          if (ch === "\r" || ch === "\n") {
            settle(() => resolve(value));
            return;
          }
          const code = ch.codePointAt(0) as number;
          if (code === 0x03) {
            settle(() => reject(new Error("aborted")));
            return;
          }
          if (code === 0x7f || code === 0x08) {
            // Drop a whole code point — halving an emoji is not a backspace.
            value = Array.from(value).slice(0, -1).join("");
            continue;
          }
          // Raw mode delivers every other control byte verbatim (Ctrl-D, the
          // ESC of an arrow key). Keeping them would corrupt the secret
          // invisibly, so they are dropped rather than stored.
          if (code < 0x20) continue;
          value += ch;
        }
      };

      // Raw mode has no EOF, but a piped or closed stream still ends; resolve
      // with what was typed instead of leaving the caller hanging.
      const onEnd = () => settle(() => resolve(value));

      input.on("data", onData);
      input.on("end", onEnd);
      input.resume();
    });
  } finally {
    setRawMode(wasRaw);
    // Nothing was echoed, so the cursor is still on the prompt line.
    io.output.write("\n");
  }
}

/** Field validators for `askValidated`; each returns `null` when the value is good. */
export const validators = {
  telegramToken: (s: string): string | null =>
    /^\d+:[A-Za-z0-9_-]{20,}$/.test(s)
      ? null
      : "That does not look like a BotFather token. Format: 1234:ABC...",

  telegramChatId: (s: string): string | null =>
    /^-?\d{5,}$/.test(s) ? null : "Chat id must be a number (negative for groups).",

  kisAccount10: (s: string): string | null =>
    /^\d{10}$/.test(s)
      ? null
      : "KIS account must be exactly 10 digits (8-digit CANO + 2-digit product code).",

  absolutePath: (s: string): string | null =>
    s.startsWith("/") ? null : "Enter an absolute path starting with /.",

  nonEmpty: (s: string): string | null =>
    s.trim().length > 0 ? null : "Value is required.",
} satisfies Record<string, (s: string) => string | null>;
