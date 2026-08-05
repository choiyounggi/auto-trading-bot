# Task 08: `init` collects the signal credentials and installs the signal jobs

## Objective
`runInit` gains a signal step that stores KRX and Brave credentials in the
Keychain, asks for the news backend, defaults `signalDir` to the package's own
`data/signals`, and offers the two signal jobs alongside the trading ones.

## Wiki pages (read these first, only these)
- wiki/security/secrets/secrets-in-code.md — use for: rule 1; the captured values
  go to the Keychain and never to `config.json` or a log line.
- wiki/infrastructure/config/environment-config.md — use for: rule 5 — optional
  keys may be skipped, and skipping must be a real path, not a silent empty write.

## Inputs
- `cli/setup.ts` — `runInit(opts)`, currently 7 steps ending with launchd install
- `cli/keychain.ts` — `keychainSet`, `KeychainLockedError`; **add**
  `SIGNAL_SERVICE = "signal-bot"` and the account constants
  `KRX_ID_ACCOUNT = "krx-id"`, `KRX_PW_ACCOUNT = "krx-pw"`,
  `BRAVE_KEY_ACCOUNT = "brave-api-key"` — these must match `src/util/keychain.py`'s
  `SIGNAL_SERVICE` and account names from task 03 exactly
- `cli/prompt.ts` — `promptSecret`, `ask`, `askDefault`, `yesNo`, `choose`,
  `askValidated`, `validators`
- `cli/config.ts` — `NEWS_BACKENDS`, `defaultSignalDir(projectDir)` (task 06),
  `JOB_KEYS` now 7 entries (task 07)
- Decisions that bind you: D2 (Telegram not re-prompted), D4 (KRX/Brave optional),
  D9 (news backend default `none`), D5 (signalDir default).

## Steps
1. Add the constants above to `cli/keychain.ts`. Do not change any existing
   export.
2. Insert a **Step 6/8 — 신호 파이프라인** between the current Python/signal-dir
   step and the bootstrap step, and renumber the headers to `N/8`:
   - `yesNo("KRX 로그인을 설정할까요? (수급 데이터 정확도가 올라갑니다)", true)`.
     If yes: `askValidated("KRX ID", validators.nonEmpty)` and
     `promptSecret("KRX 비밀번호")`, then `keychainSet(SIGNAL_SERVICE, ...)` for
     each. If no: skip, writing nothing.
   - `yesNo("Brave Search API 키를 설정할까요? (뉴스 보강용, 선택)", false)`.
     If yes: `promptSecret("Brave Search API key")` → `keychainSet`.
   - `choose("뉴스 분류 LLM", NEWS_BACKENDS, "none")` → goes into
     `cfg.newsLlmBackend`.
   - Catch `KeychainLockedError` exactly as the existing KIS step does: print its
     message and return 1 without continuing.
3. In the existing Python/signal-dir step, change the `askDefault` suggestion for
   `signalDir` from the `~/stock-signal-bot/...` literal to
   `defaultSignalDir(opts.projectDir)`. Keep the existing "directory does not
   exist" warning behaviour — but note it will now normally not exist yet on a
   fresh install, which is fine because the signal job creates it.
4. **Do not prompt for Telegram again** (D2). The existing Telegram step already
   stores it under `telegram-bot`, and `src/util/keychain.py:load_telegram_keys()`
   is what feeds the signal bot.
5. The launchd step already loops `JOB_KEYS`, so the two new jobs appear
   automatically once task 07 widens it — confirm this rather than adding a
   special case, and include their schedule text in the prompt.
6. Extend `cli/__tests__/setup.test.ts`.

## Deliverables
- `cli/setup.ts` (modified)
- `cli/keychain.ts` (modified — constants only)
- `cli/__tests__/setup.test.ts` (modified)

## Verify
- `npm test` green, with at least:
  - normal: full happy path returns 0 and `keychainSet` is called **8** times —
    3 KIS + 2 Telegram + 2 KRX + 1 Brave. Assert the count and the
    service/account pairs used for the signal three.
  - normal: declining both KRX and Brave returns 0 with `keychainSet` called 5
    times, and `cfg.newsLlmBackend === "none"`.
  - normal: the `signalDir` prompt's default is `<projectDir>/data/signals`.
  - error: `keychainSet` throwing `KeychainLockedError` during the signal step
    returns 1, and `saveConfig` is not called afterwards.
  - boundary: choosing a news backend of `"claude"` is carried into the saved
    config; choosing nothing yields `"none"`.
  - boundary: Telegram is prompted **once** — assert no prompt text mentioning a
    Telegram token appears twice in the captured output.
  - boundary: assert no captured output line contains any value fed to a prompt
    (extend the existing secret-leak assertion to the KRX/Brave inputs).
- `npm run build` exits 0.

## Out of scope
- `doctor`'s signal checks — task 09.
- The Python-side reader — task 03 owns `load_signal_keys`.
