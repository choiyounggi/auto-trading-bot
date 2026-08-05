# Task 03: load KRX and Brave credentials from the Keychain

## Objective
`load_signal_keys()` pulls the signal bot's credentials out of the macOS Keychain
and injects them into `os.environ` under the names the signal code already reads,
so no `.env` file is needed and no call site changes.

## Wiki pages (read these first, only these)
- wiki/security/secrets/secrets-in-code.md — use for: rule 1 (secrets load at
  runtime from a secret store, never from a file in the repo) and the leak-response
  table, which is why the `.env` path is removed rather than merely ignored.
- wiki/infrastructure/config/environment-config.md — use for: rule 2 (code reads
  a named value through **one** code path) — this function is that path.

## Inputs
- `src/util/keychain.py` — already exports `keychain_get(service, account)`,
  `load_kis_keys(mode)`, `load_telegram_keys()`. **Mirror `load_kis_keys`'s shape.**
- Decisions that bind you: D2 (service `signal-bot`; accounts `krx-id`, `krx-pw`,
  `brave-api-key`; Telegram is reused, not duplicated), D3 (inject into
  `os.environ`), D4 (absent credentials degrade, never raise).

## Steps
1. In `src/util/keychain.py` add:
   ```python
   SIGNAL_SERVICE = "signal-bot"

   def load_signal_keys() -> dict:
       """Keychain에서 신호 봇 키 → os.environ 자동 inject.

       KRX 로그인과 Brave 키는 **선택**이다 — 없으면 신호 봇이 기능을 줄여
       동작하므로 예외를 올리지 않고 'missing' 으로만 보고한다.
       """
   ```
   - Map exactly: `KRX_ID` ← `krx-id`, `KRX_PW` ← `krx-pw`,
     `BRAVE_SEARCH_API_KEY` ← `brave-api-key`, all under service `signal-bot`.
   - Follow `load_kis_keys`'s existing convention: if the env var is already set,
     leave it and record `"already-set"`; on a Keychain hit set it and record
     `f"keychain ({len(v)} chars)"`; on a miss record `"missing"` and set nothing.
   - Return the report dict. **Never** put a secret value in the returned dict.
2. Do not modify `load_kis_keys`, `load_telegram_keys`, or `keychain_get`.
3. Create `tests/test_signal_keychain.py`. Inject a fake `keychain_get` via
   `monkeypatch.setattr` — the tests must never touch the real Keychain, and must
   clear/restore the three env vars so they cannot leak between tests.

## Deliverables
- `src/util/keychain.py` (modified)
- `tests/test_signal_keychain.py` (new)

## Verify
- `.venv/bin/pytest tests/test_signal_keychain.py -q` passes with at least:
  - normal: all three present in the Keychain → the three env vars are set to
    those values and the report marks each `keychain (...)`.
  - normal: the service/account names used are exactly `signal-bot` +
    `krx-id`/`krx-pw`/`brave-api-key` — assert on the recorded calls, because this
    is the wire contract with `cli/keychain.ts`.
  - error: `keychain_get` raising is **not** propagated — assert
    `load_signal_keys()` returns normally and marks the affected key `missing`.
  - error: a pre-set `KRX_ID` in `os.environ` is left untouched and reported
    `already-set` (the Keychain must not overwrite an explicit override).
  - boundary: none of the three present → returns with all three `missing`, sets
    no env var, and raises nothing (D4).
  - boundary: an empty-string Keychain value counts as missing, not as a value.
  - boundary: assert the returned report contains **no** secret value, using a
    recognisable fake (`"SEKRIT"`).
- `.venv/bin/pytest -q` — whole suite green.

## Out of scope
- Calling `load_signal_keys()` from the signal entry point — task 05.
- Prompting for these values — task 08. Checking them — task 09.
