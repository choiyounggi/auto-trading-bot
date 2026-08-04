# Task 06: Python 3.11+ interpreter discovery

## Objective
`findPython()` returns the absolute path of an interpreter reporting version
3.11–3.13, or `null`. A bare `python3` is never accepted without first reading
its version.

## Wiki pages (read these first, only these)
- wiki/platforms/toolchains/version-management.md — use for: why a
  version-manager shim resolves interactively but not under a service, and the
  rule that a project pins the language version.
- wiki/platforms/environment/path-resolution.md — use for: the Instead-of row
  "calling a bare tool name in a hook/daemon/agent script → resolve to an
  absolute path with a fail-loud check".

## Inputs
- `cli/resolve-cli-path.ts` from task 05 — reuse its exported
  `isExecutableFile` and `loginShellWhich`; do not re-implement them.
- Decisions that bind you: D10 (probe candidates in order, accept the first
  reporting 3.11–3.13, store the absolute path).
- Verified fact on this machine: `python3` is 3.14.6 (too new — the engine
  requires `>=3.11` but is only validated on 3.11–3.13) while the usable
  interpreter is at
  `/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11`.

## Steps
1. Create `cli/python.ts`.
2. `export function pythonVersion(binPath: string, run = defaultRun): [number, number] | null`
   - `defaultRun` = `execFileSync(binPath, ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
     { encoding:"utf8", timeout: 5_000, stdio: ["ignore","pipe","ignore"] })`.
   - Parse `"3.11"` → `[3, 11]`. Any failure or unparseable output → `null`,
     never throws.
3. `export const PYTHON_CANDIDATES: string[]` — in this order:
   - `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13`
   - `.../3.12/bin/python3.12`
   - `.../3.11/bin/python3.11`
   - `/opt/homebrew/bin/python3.13`, `python3.12`, `python3.11`
   - `/usr/local/bin/python3.13`, `python3.12`, `python3.11`
4. `export function isSupported(v: [number, number] | null): boolean` —
   true iff `v[0] === 3 && v[1] >= 11 && v[1] <= 13`.
5. `export function findPython(opts?: { candidates?: string[]; version?: typeof pythonVersion;
   loginShell?: (cmd: string) => string | null }): string | null`
   - Walk `candidates` (default `PYTHON_CANDIDATES`); for each that
     `isExecutableFile`, take `pythonVersion` and return the path on the first
     `isSupported` hit.
   - Then try, in order, `loginShellWhich("python3.13")`, `"python3.12"`,
     `"python3.11"`, and finally `"python3"` — each still gated by
     `isSupported`, so an unsupported `python3` (like this machine's 3.14) is
     rejected rather than returned.
   - Return `null` when nothing qualifies.
6. Create `cli/__tests__/python.test.ts` with all externals injected.

## Deliverables
- `cli/python.ts`
- `cli/__tests__/python.test.ts`

## Verify
- `npm test` passes with at least these cases:
  - normal: `pythonVersion` with a stub returning `"3.11\n"` returns `[3, 11]`.
  - normal: `findPython` where the second candidate is executable and reports
    3.12 returns that second candidate's path.
  - error: `pythonVersion` with a stub that throws returns `null` and does not throw.
  - error: `findPython` with no candidates and a `loginShell` returning `null`
    returns `null`.
  - boundary: `isSupported([3,10])` is `false`, `isSupported([3,11])` is `true`,
    `isSupported([3,13])` is `true`, `isSupported([3,14])` is `false`
    (this is the exact case live on the author's machine).
  - boundary: `isSupported(null)` is `false`.
  - boundary: a candidate that is executable but reports 3.14 is **skipped**,
    and a later 3.11 candidate is returned — proving version gating, not
    first-executable-wins.

## Out of scope
- Creating the venv or installing packages (task 08).
