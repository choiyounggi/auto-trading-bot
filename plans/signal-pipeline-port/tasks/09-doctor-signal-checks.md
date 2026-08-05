# Task 09: `doctor` reports on the signal half of the pipeline

## Objective
`runDoctor` adds checks for the KRX credentials, the news backend, and the
freshness of the signal directory as a *producer* output, so a user can tell
whether the chain will actually trade tomorrow.

## Wiki pages (read these first, only these)
- wiki/platforms/processes/background-services.md — use for: rule 4, verify by
  observing (`launchctl list`), which is how the two new jobs are reported.
- wiki/infrastructure/config/environment-config.md — use for: rules 3–4, the
  schema is the key inventory, so every config key gets a check.

## Inputs
- `cli/doctor.ts` — `runDoctor(deps?)`, `formatChecks`, `exitCodeFor`, `Check`,
  `CheckStatus`, and the existing `signal-dir` check
- `cli/keychain.ts` — `keychainHas`, `SIGNAL_SERVICE`, `KRX_ID_ACCOUNT`,
  `KRX_PW_ACCOUNT`, `BRAVE_KEY_ACCOUNT` (task 08)
- `cli/config.ts` — `newsLlmBackend`, `JOB_KEYS` (7 entries)
- `cli/launchd.ts` — `jobStatus`
- Decisions that bind you: D4 (KRX and Brave absent → `warn`, never `fail` — they
  are optional and the pipeline degrades rather than breaks).

## Steps
1. Add a `keychain-krx` check: both `krx-id` and `krx-pw` present → `pass`;
   **neither** present → `warn` with detail explaining the signal bot falls back
   to unauthenticated KRX access; **exactly one** present → `fail` (a half-entered
   credential pair is a misconfiguration, not a deliberate skip).
2. Add a `keychain-brave` check: present → `pass`; absent → `warn` with detail
   that news enrichment is disabled.
3. Add a `news-backend` check: `cfg.newsLlmBackend === "none"` → `pass` with
   detail "disabled (no LLM cost)"; otherwise resolve that agent through the
   existing `detectAgents()` and `pass` when found, `fail` when the configured
   backend's binary is missing — a configured-but-absent backend silently
   produces no news signal.
4. Extend the existing `signal-dir` check's hint. It currently names
   `stock-signal-bot` as an external prerequisite; now that the producer ships in
   this package the hint must instead point at `kis-trader start signalKr` to
   generate a file on demand. Keep the freshness thresholds unchanged (72 h).
5. The per-job checks already loop `JOB_KEYS`, so `signalKr`/`signalUs` appear
   automatically once task 07 widens it — confirm rather than special-case.
6. Extend `cli/__tests__/doctor.test.ts`.

## Deliverables
- `cli/doctor.ts` (modified)
- `cli/__tests__/doctor.test.ts` (modified)

## Verify
- `npm test` green, with at least:
  - normal: all-healthy stubs → every check `pass`, `exitCodeFor` is 0, and the
    checks array contains `keychain-krx`, `keychain-brave`, `news-backend`.
  - normal: `newsLlmBackend: "none"` → `news-backend` is `pass` even though no
    agent was detected (the "no LLM cost" path must not be reported as broken).
  - error: exactly one of `krx-id` / `krx-pw` present → `keychain-krx` is `fail`
    and `exitCodeFor` is 1; the detail names the missing account.
  - error: `newsLlmBackend: "codex"` with `detectAgents` returning no codex →
    `news-backend` is `fail`.
  - boundary: neither KRX item present → `warn`, and `exitCodeFor` stays 0 when
    nothing else failed (D4 — optional means optional).
  - boundary: Brave absent → `warn`, not `fail`.
  - boundary: `signalKr` enabled in config but `jobStatus` `"absent"` → that job
    check is `fail`; disabled and absent → `warn`. (The loop already does this —
    assert it now covers 7 jobs.)
  - boundary: the `signal-dir` hint no longer mentions `stock-signal-bot` —
    assert the string, since the prerequisite genuinely changed.
- `node dist/index.js doctor` on a machine with no config still exits 1 with a
  single `config` check (the existing short-circuit must survive).

## Out of scope
- Running the signal producer to test it live — `doctor` checks configuration and
  observable state, not a full pipeline execution.
