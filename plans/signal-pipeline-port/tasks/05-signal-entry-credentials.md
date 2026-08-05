# Task 05: the signal entry point takes its credentials from the Keychain

## Objective
`python -m src.signal.main` gets `KRX_ID`, `KRX_PW`, `BRAVE_SEARCH_API_KEY` and
the Telegram pair from the Keychain instead of a `.env` file, and no `.env` is
read anywhere in the signal tree.

## Wiki pages (read these first, only these)
- wiki/security/secrets/secrets-in-code.md — use for: rule 1 (load at runtime
  from the secret store) and rule 2's inverse — an untracked `.env` is the pattern
  being *removed* here, because the package now has a real secret store.

## Inputs
- `src/signal/main.py` from task 01 — currently calls `load_dotenv(ROOT / ".env")`
  at what was line 181 (the line number moved with the copy; find it by the call)
- `src/util/keychain.py` from task 03 — `load_signal_keys()` and the existing
  `load_telegram_keys()`
- Decisions that bind you: D2 (Telegram is reused from `telegram-bot`, never
  duplicated into `signal-bot`), D3 (env injection), D4 (missing keys degrade).

## Steps
1. In `src/signal/main.py`, replace the `load_dotenv(ROOT / ".env")` call with:
   ```python
   from src.util.keychain import load_signal_keys, load_telegram_keys
   load_signal_keys()
   load_telegram_keys()
   ```
   `load_telegram_keys()` is what supplies `TELEGRAM_BOT_TOKEN` /
   `TELEGRAM_CHAT_ID` to `src/signal/notify/telegram_bot.py`, which reads them
   through `os.getenv` — that module needs no change.
2. Remove the now-unused `from dotenv import load_dotenv` import from
   `src/signal/main.py`. Leave the `python-dotenv` dependency in `pyproject.toml`
   alone (D11).
3. Log the two report dicts at INFO the way the trader's entry points do, so a
   launchd run records which credentials resolved. **Never log a value** — the
   reports carry only lengths and status strings.
4. Sweep the rest of the vendored tree for `.env` reads:
   `grep -rn "dotenv\|\.env" src/signal/` must come back empty afterwards.
5. Create `tests/test_signal_entry_credentials.py`. Import
   `src.signal.main` and assert on module structure — do **not** execute `run()`,
   which performs network calls to KRX and Telegram.

## Deliverables
- `src/signal/main.py` (modified)
- `tests/test_signal_entry_credentials.py` (new)

## Verify
- `grep -rn "dotenv" src/signal/ | wc -l` is **0**. Paste the number.
- `grep -rn "load_signal_keys\|load_telegram_keys" src/signal/main.py` shows both
  calls.
- `.venv/bin/pytest tests/test_signal_entry_credentials.py -q` passes with at least:
  - normal: importing `src.signal.main` does not raise.
  - normal: `src.signal.main` exposes `load_signal_keys` and `load_telegram_keys`
    in its namespace (proving the import replaced the dotenv path).
  - error: with `load_signal_keys` monkeypatched to raise, importing the module
    still succeeds — credential loading must happen inside `run()`/`main()`, not
    at import time, or a launchd job dies before it can log anything.
  - boundary: `src.signal.main` has **no** attribute `load_dotenv`.
- `.venv/bin/python -c "import src.signal.main; print('ok')"` prints `ok`.
- `.venv/bin/pytest -q` — whole suite green.

## Out of scope
- Where the values come from — task 03 owns the Keychain reader; task 08 owns
  prompting for them.
- `src/signal/notify/telegram_bot.py` — it already reads `os.getenv` and must not
  be changed.
