# Task 01: relocate the signal bot into `src/signal/`

## Objective
Every non-backup Python module from `~/stock-signal-bot/src` exists under
`src/signal/` in this repo, with internal imports rewritten, and
`python -c "import src.signal.main"` succeeds.

## Wiki pages (read these first, only these)
- None. `[no-wiki]` — a mechanical namespace move; the design decision (D1) is
  already made and forced by a measured collision.

## Inputs
- Source tree: `~/stock-signal-bot/src` (read-only — do not modify it)
- The exact 23 files to port:
  `__init__.py`, `main.py`, `universe.py`,
  `analysis/{__init__,flow_analyzer,llm_analyzer,overseas_strategy_builder,price_analyzer,signal_engine,strategy_builder,ticker_context}.py`,
  `data/{__init__,dump_signals,macro_context,naver_finance,naver_source,news_brave,overseas_yfinance_source,pykrx_source,short_balance,sources}.py`,
  `notify/{__init__,telegram_bot}.py`
- The 3 existing tests: `~/stock-signal-bot/tests/test_{signal_engine,strategy_builder,overseas_strategy_builder}.py`
- Decisions that bind you: D1 (`src/signal/` namespace), D13 (what not to port),
  D14 (port the 3 tests into `tests/signal/`).

## Why the namespace move is mandatory
`src/notify/` already exists in this repo (the trader's Telegram sender), and the
trader has a `src/universe/` **package** while the signal bot has a
`src/universe.py` **module**. Copying the signal bot to `src/` directly would
overwrite one and make the other unimportable.

## Steps
1. Create `src/signal/` and copy the 23 files listed above into it, preserving
   the `analysis/`, `data/`, `notify/` subdirectory layout.
   **Copy nothing else** — no `*.bak*`, no `__pycache__`, no `.env`, no `.venv`.
2. Rewrite every intra-project import in the copied files:
   `from src.X` → `from src.signal.X`, `import src.X` → `import src.signal.X`.
   This covers `src.analysis.*`, `src.data.*`, `src.notify.*`, `src.universe`,
   and any `from src import ...`.
   Leave stdlib and third-party imports untouched.
3. `src/signal/main.py` computes a `ROOT` path (used for `.env` and config
   lookups). It now sits one level deeper, so any
   `Path(__file__).resolve().parents[N]` must have `N` incremented by 1 to keep
   pointing at the repo root. Find every such expression in the copied tree and
   fix it. Do not change what the path is *used for* — task 05 handles `.env`.
4. Create `tests/signal/__init__.py` and copy the 3 test files there, applying
   the same import rewrite (they import `src.analysis.*` today).
5. Add `src/signal/__init__.py` if the copy did not produce one.

## Deliverables
- `src/signal/**` (23 modules)
- `tests/signal/**` (4 files: `__init__.py` + 3 tests)

## Verify
- `python -c "import src.signal.main"` exits 0 — run it with the repo's venv from
  the repo root. This is the single check that proves the tree resolves.
- `grep -rnE "^\s*(from|import) src\.(analysis|data|notify|universe)\b" src/signal/ tests/signal/`
  returns **0 lines** — no un-rewritten import survives. Paste the count.
- `grep -rn "\.bak" src/signal/ | wc -l` is 0, and
  `find src/signal -name "*.bak*" | wc -l` is 0.
- `.venv/bin/pytest tests/signal -q` runs the 3 ported tests. Record the result.
  If a test fails for a reason unrelated to imports (a missing optional
  dependency, a network call), report it in the task report rather than editing
  the analyser — task 02 installs the dependencies and the analysers are out of
  scope (D12).
- `.venv/bin/pytest -q` — the trader's existing 207 tests still pass.

## Out of scope
- `pyproject.toml` dependencies — task 02.
- Replacing `load_dotenv` — task 05.
- The atomic-write fix in `dump_signals.py` — task 04.
