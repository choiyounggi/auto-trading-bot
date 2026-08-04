# Task 13: make the signal directory configurable (Python)

## Objective
The orchestrator reads its signal directory from `KIS_TRADER_SIGNAL_DIR` when
set, falling back to today's hardcoded `~/stock-signal-bot/data/signals`, so a
freshly-installed user can point it anywhere without editing source.

## Wiki pages (read these first, only these)
- wiki/infrastructure/config/environment-config.md — use for: rule 2 (config
  lives outside the artifact; code reads a named value through one code path)
  and the Instead-of row against hardcoding an environment-specific path.

## Inputs
- `src/orchestrator/__main__.py` line 87 — the current hardcoded
  `signal_dir = Path.home() / "stock-signal-bot" / "data" / "signals"`
- `scripts/test_entry_decision.py` line 40 — the same literal path
- Decisions that bind you: D12 (env var name `KIS_TRADER_SIGNAL_DIR`; the
  fallback is preserved so the author's running install keeps working).

## Steps
1. Add to `src/orchestrator/signal_loader.py` a single resolver used by every
   caller — one code path, per the wiki rule:
   ```python
   DEFAULT_SIGNAL_DIR = Path.home() / "stock-signal-bot" / "data" / "signals"

   def resolve_signal_dir(env: Mapping[str, str] | None = None) -> Path:
       """KIS_TRADER_SIGNAL_DIR 우선, 미설정 시 기존 기본 경로."""
   ```
   - `env` defaults to `os.environ` (injectable for tests).
   - A set-but-empty or whitespace-only value is treated as unset.
   - `~` in the value is expanded (`Path(v).expanduser()`).
   - A relative value raises `ValueError("KIS_TRADER_SIGNAL_DIR must be an absolute path: <value>")`
     — a relative path would resolve against launchd's working directory and
     silently find nothing.
2. Replace the literal in `src/orchestrator/__main__.py` with
   `signal_dir = resolve_signal_dir()` and import it from `signal_loader`.
   Change nothing else in that file.
3. Replace the literal in `scripts/test_entry_decision.py` the same way.
4. Create `tests/test_signal_dir.py`.

## Deliverables
- `src/orchestrator/signal_loader.py` (modified)
- `src/orchestrator/__main__.py` (modified)
- `scripts/test_entry_decision.py` (modified)
- `tests/test_signal_dir.py` (new)

## Verify
- `.venv/bin/pytest tests/test_signal_dir.py -q` passes with at least:
  - normal: `resolve_signal_dir({"KIS_TRADER_SIGNAL_DIR": "/tmp/sig"})` returns
    `Path("/tmp/sig")`.
  - normal: `resolve_signal_dir({})` returns `DEFAULT_SIGNAL_DIR` — the
    backward-compatible fallback.
  - error: `resolve_signal_dir({"KIS_TRADER_SIGNAL_DIR": "relative/path"})`
    raises `ValueError` whose message contains
    `"must be an absolute path"` (assert the type **and** the message).
  - boundary: `""` and `"   "` both fall back to `DEFAULT_SIGNAL_DIR`.
  - boundary: `"~/sigs"` expands to `Path.home() / "sigs"`.
- `.venv/bin/pytest -q` — the whole existing suite still passes.
- `grep -n "stock-signal-bot" src/orchestrator/__main__.py` returns no
  path-construction line (only the resolver import / comments may mention it).

## Out of scope
- Porting the stock-signal-bot project itself. That is a separate product and
  is explicitly excluded from this plan.
- Writing the env var into the plists — task 07 already does that.
