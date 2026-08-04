# Task 05: local LLM CLI discovery

## Objective
`resolveCliPath("claude" | "codex" | "pi" | "gemini")` returns the absolute path
of that CLI on this machine, or `null`, using well-known paths → nvm scan →
login-shell lookup, with every fallback injectable for tests.

## Wiki pages (read these first, only these)
- wiki/platforms/environment/path-resolution.md — use for: the per-context PATH
  table (a daemon/agent does not inherit the interactive shell's PATH) and the
  Instead-of row "calling a bare tool name in a hook/daemon/agent script → resolve
  to an absolute path with a fail-loud check".
- wiki/platforms/processes/non-interactive-cli-invocation.md — use for: rule 1
  (detach fd 0 with `</dev/null`) and rule 3 (bound the call with a timeout)
  when probing a CLI's `--version`.

## Inputs
- Reference implementation to port (read it, then write the Node version):
  `/Users/choeyeong-gi/Desktop/workspace/cliclaw/lib/resolve-cli-path.ts`
- Decisions that bind you: D11 (three-stage resolution, sentinel-filtered login
  shell, version probe with stdin detached and a timeout), D2 (zero deps).

## Steps
1. Create `cli/resolve-cli-path.ts`. Port the cliclaw module — it is already
   pure `node:` API, so the port is: keep `isExecutableFile`, `parseNodeVersion`,
   `pickNvmCandidate`, `resolveNvmDir`, `loginShellWhich`, `resolveCliPath`,
   `clearResolveCache`, the `SupportedCli` type, and the `ResolveOptions`
   injection points (`noCache`, `env`, `loginShell`, `wellKnown`) **unchanged in
   behaviour**. Adjust only what Bun-vs-Node requires (imports stay `node:*`;
   drop the `/// <reference types="bun" />` style comments).
2. Add one new export the reference does not have:
   `export function probeVersion(binPath: string, run = defaultProbe): string | null`
   - `defaultProbe` = `execFileSync(binPath, ["--version"], { encoding:"utf8",
     timeout: 5_000, stdio: ["ignore","pipe","ignore"] })` — stdin is `"ignore"`,
     which is the fd-0 detachment the wiki requires, and the timeout is the bound.
   - Returns the first line trimmed to at most 60 characters, or `null` on any
     failure (non-zero exit, timeout, missing binary). Never throws.
3. Add `export function detectAgents(opts?: ResolveOptions): Record<SupportedCli, { path: string; version: string | null } | null>`
   returning an entry for all four CLIs, `null` where not found.

## Deliverables
- `cli/resolve-cli-path.ts`
- `cli/__tests__/resolve-cli-path.test.ts`

## Verify
- `npm test` passes with at least these cases:
  - normal: `resolveCliPath("claude", { noCache: true, wellKnown: () => [<a real
    executable created in a temp dir> ] })` returns that path.
  - normal: `parseNodeVersion("v22.10.0")` returns `[22,10,0]`.
  - normal: `probeVersion` with a stub returning `"1.2.3\nextra"` returns `"1.2.3"`.
  - error: `resolveCliPath("pi", { noCache:true, wellKnown:()=>[], env:{},
    loginShell:()=>null })` returns `null` (all three stages miss).
  - error: `probeVersion` with a stub that throws returns `null` and does not throw.
  - boundary: `parseNodeVersion("system")` and `parseNodeVersion("lts/*")` each
    return `null`.
  - boundary: `pickNvmCandidate` over a temp nvm dir holding `v18.0.0` and
    `v20.1.0` (both with an executable `bin/claude`) returns the **v20.1.0** path.
  - boundary: `isExecutableFile` on a directory returns `false`, and on a
    non-executable regular file returns `false`.
  - boundary: `probeVersion` with a stub returning a 200-character single line
    returns a string of length exactly 60.

## Out of scope
- Choosing the default agent and writing it to config (task 10).
