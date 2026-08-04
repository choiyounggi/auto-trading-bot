# Task 12: CLI entry point, subcommand dispatch, help

## Objective
`node dist/index.js <command>` dispatches every subcommand, prints the banner on
interactive commands, prints usage for `help`/`--help`/`-h`/no args, and exits
`2` on an unknown command.

## Wiki pages (read these first, only these)
- None. `[no-wiki]` — argument dispatch and usage text; the decisions the wiki
  owns were already made in tasks 03–11.

## Inputs
- `cli/banner.ts` — `printBanner`, `readPackageVersion` (task 02)
- `cli/config.ts` — `configHome`, `loadConfig` (task 03)
- `cli/setup.ts` — `runInit` (task 10)
- `cli/doctor.ts` — `runDoctor`, `formatChecks`, `exitCodeFor` (task 11)
- `cli/launchd.ts` — `installJob`, `uninstallJob`, `jobStatus`, `JOBS`, `JobKey` (task 07)
- `cli/bootstrap.ts` — `venvPython` (task 08), used by `start`
- Decisions that bind you: D1 (`bin` → `dist/index.js`, ESM, shebang).

## Steps
1. Create `cli/index.ts` starting with the exact line `#!/usr/bin/env node`.
   Confirm `tsc` preserves it as the first line of `dist/index.js`; if it does
   not, prepend it in a `build` step rather than hand-editing `dist/`.
2. Export `export function parseArgv(argv: string[]): { cmd: string; rest: string[] }`
   — pure, testable: `argv` is `process.argv.slice(2)`; an empty array yields
   `cmd: "help"`.
3. Export `export const COMMANDS` — an ordered array of
   `{ name: string; summary: string; interactive: boolean }` covering:
   `init`, `doctor`, `start`, `logs`, `install-jobs`, `uninstall-jobs`,
   `status`, `upgrade`, `help`.
4. Export `export function usage(version: string): string` built **from
   `COMMANDS`**, so a new command cannot be added without appearing in help.
   Append an `Env:` section documenting `KIS_TRADER_HOME` and `KIS_MODE`.
5. `main()` behaviour:
   - Print the banner for every command whose `interactive` is true. `start`
     and `logs` are `interactive: false` — their output is a live stream that
     the art would push off-screen.
   - `init` → `runInit({ home: configHome(), projectDir: <package root> })`.
   - `doctor` → `runDoctor()`, write `formatChecks(...)`, exit `exitCodeFor(...)`.
     Support `doctor --json`, which writes `JSON.stringify(checks, null, 2)`
     instead and uses the same exit code.
   - `start` → resolve the config, then `spawn(venvPython, ["-m", <module>], { stdio:"inherit", cwd: cfg.projectDir })`
     where `<module>` comes from a required positional: `kis-trader start monitor`.
     An unknown or missing job name prints the valid names and exits `2`.
   - `logs` → `spawn("tail", ["-F", join(configHome(),"logs", <file>)], { stdio:"inherit" })`,
     default file `orchestrator.log`; `--err` selects `<job>.err`. A missing
     file exits `1` with the path named.
   - `install-jobs` / `uninstall-jobs` → loop the `JobKey`s enabled in config.
   - `status` → print `jobStatus` for every job (no other checks).
   - `upgrade` → `npm install -g @younggichoi/kis-trader@latest` via `spawn`
     with `stdio:"inherit"`, then re-run `install-jobs` so the plists point at
     the new package path.
   - `help`/`--help`/`-h`/none → `usage(version)`, exit 0.
   - unknown → write `Unknown command: <cmd>` to stderr, then usage, exit `2`.
6. Wrap `main()` in `.catch()` that writes the stack to stderr and exits 1.
7. Create `cli/__tests__/cli.test.ts`. Test the pure exports (`parseArgv`,
   `usage`, `COMMANDS`); do not execute `main()`.

## Deliverables
- `cli/index.ts`
- `cli/__tests__/cli.test.ts`

## Verify
- `npm test` passes with at least these cases:
  - normal: `parseArgv(["doctor","--json"])` → `{ cmd:"doctor", rest:["--json"] }`.
  - normal: `usage("1.0.0")` contains every `COMMANDS[i].name`, asserted by
    iterating `COMMANDS` — this is the check that help cannot drift.
  - error: `parseArgv([])` returns `cmd === "help"` (not an empty string).
  - boundary: `COMMANDS` contains `start` and `logs` with `interactive === false`
    and `init`/`doctor` with `interactive === true`.
  - boundary: `usage("")` still lists all commands.
- `npm run build && node dist/index.js help` exits 0 and prints the banner plus
  usage.
- `node dist/index.js totally-unknown; echo $?` prints `2`.
- `head -1 dist/index.js` is exactly `#!/usr/bin/env node`.

## Out of scope
- README (task 16).
