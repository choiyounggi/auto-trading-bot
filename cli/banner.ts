/**
 * Terminal banner for interactive `kis-trader` commands.
 *
 * The art is "ANSI Shadow" spelling KIS TRADER, embedded inline so the package
 * keeps zero runtime dependencies. `renderBanner` is pure so it can be asserted
 * on directly; `printBanner` is the thin side-effecting wrapper.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

/** One entry per row of the art. Exported so tests can assert on it. */
export const ART: readonly string[] = [
  "██╗  ██╗██╗███████╗  ████████╗██████╗  █████╗ ██████╗ ███████╗██████╗ ",
  "██║ ██╔╝██║██╔════╝  ╚══██╔══╝██╔══██╗██╔══██╗██╔══██╗██╔════╝██╔══██╗",
  "█████╔╝ ██║███████╗     ██║   ██████╔╝███████║██║  ██║█████╗  ██████╔╝",
  "██╔═██╗ ██║╚════██║     ██║   ██╔══██╗██╔══██║██║  ██║██╔══╝  ██╔══██╗",
  "██║  ██╗██║███████║     ██║   ██║  ██║██║  ██║██████╔╝███████╗██║  ██║",
  "╚═╝  ╚═╝╚═╝╚══════╝     ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚══════╝╚═╝  ╚═╝",
];

const RESET = "\x1b[0m";
const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";

/** Green → cyan, applied row by row so the mark reads as one solid shape. */
const GRADIENT: readonly string[] = [
  "\x1b[38;5;42m",
  "\x1b[38;5;48m",
  "\x1b[38;5;50m",
  "\x1b[38;5;50m",
  "\x1b[38;5;48m",
  "\x1b[38;5;42m",
];

export const REPO_URL = "https://github.com/choiyounggi/auto-trading-bot";

/**
 * Read `version` out of `<pkgDir>/package.json`.
 *
 * Never throws: a missing file, unreadable path, unparseable JSON, or a
 * non-string `version` all yield `"?"`. The banner is decoration — it must
 * never be the reason a command fails.
 */
export function readPackageVersion(pkgDir: string): string {
  try {
    const raw = readFileSync(join(pkgDir, "package.json"), "utf8");
    const pkg: unknown = JSON.parse(raw);
    if (
      typeof pkg === "object" &&
      pkg !== null &&
      typeof (pkg as { version?: unknown }).version === "string"
    ) {
      return (pkg as { version: string }).version;
    }
    return "?";
  } catch {
    return "?";
  }
}

/** Build the full banner text, including the trailing newline. */
export function renderBanner(version: string): string {
  const lines: string[] = [""];
  for (let i = 0; i < ART.length; i++) {
    lines.push("  " + (GRADIENT[i] ?? "") + ART[i] + RESET);
  }
  lines.push("");
  lines.push(
    `  ${BOLD}LLM-driven KIS auto-trading${RESET} ` +
      `${DIM}— 한국투자증권 · Telegram · launchd${RESET}`,
  );
  lines.push(`  ${DIM}v${version}  ·  ${REPO_URL}${RESET}`);
  lines.push("");
  return lines.join("\n") + "\n";
}

/** Write the banner. Callers suppress it by simply not calling this. */
export function printBanner(
  version: string,
  out: NodeJS.WritableStream = process.stdout,
): void {
  out.write(renderBanner(version));
}
