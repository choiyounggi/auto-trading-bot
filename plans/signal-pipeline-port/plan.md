# signal-pipeline-port

Goal: absorb `~/stock-signal-bot` (the signal producer) into
`@younggichoi/kis-trader` so a single `npm i -g @younggichoi/kis-trader &&
kis-trader init` yields the whole chain — signal generation → Telegram alert →
automated trading — with no second project to install.

Acceptance criteria:
- `python -m src.signal.main --dry-run` runs from the package and writes a
  signal JSON that validates against the repo's own `schemas/signal-v1.json`.
- That JSON lands in the same directory `src/orchestrator/signal_loader.py`
  reads (`KIS_TRADER_SIGNAL_DIR`, default `<projectDir>/data/signals`).
- No `.env`: KRX and Brave credentials live in the Keychain beside the KIS and
  Telegram ones; Telegram is **not** re-prompted (the trader already stores it).
- `kis-trader init` installs 7 launchd jobs (5 trading + 2 signal) and
  `kis-trader doctor` reports on the signal half too.
- `npm test` and `pytest` green; `npm pack --dry-run` still excludes tests/plans.
- NOT done here: `npm publish`.

Stack: unchanged — TypeScript CLI (Node ≥ 20, zero runtime deps, `node:test`)
over a Python 3.11–3.13 engine (pytest). macOS only.

Measured facts this plan rests on (verified 2026-08-05, not assumed):
- 23 non-`.bak` Python files under `~/stock-signal-bot/src` (~4459 lines total
  including backups).
- **Namespace collision is real and forces D1**: `src/notify/` exists in *both*
  projects, and the signal bot's `src/universe.py` (module) collides with the
  trader's `src/universe/` (package).
- `src/main.py:181` calls `load_dotenv(ROOT / ".env")`; `src/main.py:395` calls
  `dump_signals_json`.
- `src/data/dump_signals.py` writes with a plain `out_path.write_text(...)` —
  **not atomic**.
- `src/data/sources.py:15` treats `KRX_ID`/`KRX_PW` as *optional* (it branches on
  presence), and `news_brave.py` likewise degrades without `BRAVE_SEARCH_API_KEY`.
- Signal jobs today: KR weekdays 16:30; US weekdays **22:35 and 23:35** (two
  times per weekday — the existing `renderPlist` only emits one).

## Decisions

| # | Decision | Choice | Wiki basis |
|---|----------|--------|------------|
| D1 | Where the vendored code lives | `src/signal/` — every module moves under it and every internal import is rewritten `src.X` → `src.signal.X`. Forced by the measured collision above, not a preference. `[no-wiki]` | — |
| D2 | Credential storage | Keychain service **`signal-bot`**, accounts `krx-id`, `krx-pw`, `brave-api-key`. Telegram is **reused** from the existing `telegram-bot` service — never prompted twice, never duplicated. `.env` support is removed entirely. | security-secrets-secrets-in-code (rule 1) |
| D3 | How Python reads them | Extend `src/util/keychain.py` with `load_signal_keys()` mirroring the existing `load_kis_keys()`: read from Keychain, inject into `os.environ` under the names the signal code already uses (`KRX_ID`, `KRX_PW`, `BRAVE_SEARCH_API_KEY`). The signal modules keep reading `os.getenv` — no call-site churn. | security-secrets-secrets-in-code (rule 1); infrastructure-config-environment-config (rule 2, one code path) |
| D4 | Missing KRX / Brave behaviour | **Degrade, do not crash.** Both are measured-optional. `doctor` reports their absence as `warn`, never `fail`. This is deliberate divergence from the "required keys crash at startup" rule: these keys are genuinely optional inputs, so making them required would break a working configuration. | infrastructure-config-environment-config (rule 5 — and its limit: the rule governs *required* keys) |
| D5 | Signal output location | `dump_signals_json` resolves its out-dir from `KIS_TRADER_SIGNAL_DIR`, falling back to `<projectDir>/data/signals` — the same resolver the trader reads through. `init`'s default for `signalDir` changes from `~/stock-signal-bot/data/signals` to `<projectDir>/data/signals`. This is what closes the loop. | infrastructure-config-environment-config (rule 2) |
| D6 | Partial-read hazard | `dump_signals_json` writes to `<name>.tmp` in the destination directory then `os.replace()`s it into place. The signal job at 16:30 can still be writing when the trader reads at 16:45; a plain `write_text` lets the trader parse a truncated JSON. `os.replace` is atomic within a filesystem. | backend-common-jobs-scheduled-job-overlap (rules 1, 3 — assume overlap; bound the run) |
| D7 | Signal job overlap + hang | Each signal job's `ProgramArguments` wraps the interpreter in `/usr/bin/timeout` so a hung run cannot block the next schedule, and the run takes a `flock`-style lockfile under the state dir so two runs never overlap. Bound: **900 s** for KR, **900 s** for US. | backend-common-jobs-scheduled-job-overlap (rules 1–3) |
| D8 | Multi-time schedules | `JobSpec.schedule` gains a `times: {hour, minute}[]` form; `renderPlist` emits one `StartCalendarInterval` dict per (weekday × time). `signalUs` uses `[{22,35},{23,35}]` → 10 dicts. The existing single-time and `intervalSec` forms keep working unchanged. | platforms-processes-background-services (macOS `StartCalendarInterval` row) |
| D9 | News LLM backend | New config key `newsLlmBackend`, allowed `none\|claude\|codex\|pi`, **default `none`** (zero cost). Deliberately *not* derived from `llmAgent`: `llmAgent` picks the model that makes trading decisions, this one only classifies news headlines, and collapsing them would silently start billing the trading agent for enrichment work. | infrastructure-config-environment-config (rule 2 — a named value per behaviour) |
| D10 | New Python dependencies | Add `pykrx>=1.0.45`, `pandas>=2.0`, `numpy>=1.24`, `beautifulsoup4>=4.12`, `lxml>=5.0`, `yfinance>=0.2`. `pyyaml`, `requests`, `python-dotenv` are already present. Each is a protocol client or parser — code you must not hand-roll. They install at `init` time via pip, so the npm tarball is unaffected. | security-dependencies-supply-chain (rule 2 add-vs-write; rule 3 verify exact names) |
| D11 | `python-dotenv` | **Keep the dependency, drop the usage.** `load_dotenv` disappears from the signal entry point (D3 replaces it), but the trader's `pyproject` already declared it and other code may import it; removing it is a separate concern. `[no-wiki]` | — |
| D12 | Test scope | Contract surface only, per the user's decision: JSON schema conformance, keychain loading, out-dir resolution, atomic write, config parsing, job table, CLI checks. The flow/price/strategy analysers are **not** given new tests — preserving their behaviour is the goal, and inventing "correct" trading outputs would be fabrication. | testing-quality-minimum-case-set (rules 1, 4) |
| D13 | Not ported | `.env` (holds live KRX/Brave secrets), `.venv/`, `__pycache__/`, `.pytest_cache/`, every `*.bak*`, `scripts/install_macbook_home.sh`, `scripts/com.choeyeonggi.*.plist`, and `data/` artifacts. `[no-wiki]` | — |
| D14 | The signal bot's own 3 tests | Ported alongside the source into `tests/signal/`, with imports rewritten. They are the only existing guard on the analysers; dropping them during a namespace move would remove the safety net exactly when it is needed. | testing-quality-minimum-case-set (rule 5 — a guard proves the move preserved behaviour) |

### Bound waiver — task 01

Task 01 moves 23 files at once, over the plan's own "≤ 3 files" rule. The waiver
is deliberate: a namespace move is atomic by nature — `src/signal/main.py`
cannot import `src.signal.data.sources` until both exist, so any split leaves an
unimportable tree and no task in the split is independently verifiable. It is one
concern ("relocate and rewrite imports"), it is mechanical, and it has a single
sharp check (`python -c "import src.signal.main"` plus a grep proving no
`from src.` / `import src.` line outside `src.signal.` survives).

## Task order

Each wave's tasks touch disjoint files — verified below, because two sessions
editing one file is the failure mode this column exists to prevent.

| Task | Depends on | Wave | Files it owns |
|------|-----------|------|---------------|
| 01-vendor-signal-source | — | 1 | `src/signal/**`, `tests/signal/**` |
| 02-python-dependencies | — | 1 | `pyproject.toml` |
| 03-keychain-signal-keys | — | 1 | `src/util/keychain.py`, `tests/test_signal_keychain.py` |
| 10-signal-config-files | — | 1 | `config/{thresholds.yaml,watchlist.txt,overseas_watchlist.yaml}` |
| 04-atomic-signal-write | 01 | 2 | `src/signal/data/dump_signals.py`, `tests/test_dump_signals_atomic.py` |
| 05-signal-entry-credentials | 01, 03 | 2 | `src/signal/main.py`, `tests/test_signal_entry_credentials.py` |
| 06-config-cli-keys | — | 3 | `cli/config.ts`, `cli/__tests__/config.test.ts` |
| 11-schema-conformance-test | 01, 04 | 3 | `tests/test_signal_schema_contract.py` |
| 07-launchd-signal-jobs | 06 | 4 | `cli/launchd.ts`, `cli/__tests__/launchd.test.ts` |
| 08-init-signal-onboarding | 03, 05, 06, 07 | 5 | `cli/setup.ts`, `cli/keychain.ts`, `cli/__tests__/setup.test.ts` |
| 09-doctor-signal-checks | 03, 06, 07, 08 | 5 | `cli/doctor.ts`, `cli/__tests__/doctor.test.ts` |
| 12-readme-and-pack | 01–11 | 6 | `README.md` |

### Seam defects found and repaired during the self-check

Two collisions would have put sessions on the same file:

1. **Tasks 06 and 07 both edited `cli/config.ts`** — 07 needed `JOB_KEYS` widened
   to seven. Repaired by making **06 the single owner of `cli/config.ts`**: it now
   does both the `newsLlmBackend` addition and the `JOB_KEYS` widening, and 07
   moved to wave 4 to consume the result. 07 is instructed to report BLOCKED
   rather than edit `cli/config.ts` if the widening is missing.
2. **Task 10 inspected `src/signal/`** while task 01 was creating it in the same
   wave. Repaired by removing that step: task 10 only places the three files in
   `config/`, and every path expression inside `src/signal/` belongs to task 01.

Task 09 also gained a dependency on 08, since it consumes the Keychain account
constants (`SIGNAL_SERVICE`, `KRX_ID_ACCOUNT`, …) that 08 adds to `cli/keychain.ts`.
