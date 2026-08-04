# Task 11b: machine-readable KIS connectivity probe (Python)

## Objective
`python -m src.broker.probe` performs one real KIS round-trip using the
credentials already in the Keychain and prints a single JSON object on stdout,
so the TypeScript `doctor` can report live API reachability without ever
handling the credentials itself.

## Wiki pages (read these first, only these)
- wiki/backend/common/reliability/timeouts-and-retries.md — use for: rule 1
  (every outbound call gets an explicit timeout) and the failure-type table, to
  classify what the probe reports.
- wiki/security/secrets/secrets-in-code.md — use for: rule 1 and the edge-case
  row on secrets reaching logs — the probe's JSON must never carry the app key,
  secret, or full account number.

## Inputs
- `src/broker/kis_client.py` — `KisClient`, `KisClient.get_balance`
- `src/util/keychain.py` — `load_kis_keys`
- Decisions that bind you: D6 (Keychain is the only credential source),
  D18 (probe contract below).

## Steps
1. Create `src/broker/probe.py` with `def probe(mode: str = "paper") -> dict`
   and a `main()` that prints `json.dumps(probe(...))` and returns exit code
   `0` when `ok` is true, else `1`. Read the mode from `KIS_MODE`, defaulting
   to `paper`.
2. The returned dict is exactly this contract (D18) — no extra keys:
   ```python
   {
     "ok": bool,          # a balance was retrieved
     "mode": "paper"|"real",
     "base_url": str,     # non-secret
     "cano_masked": str,  # last 4 digits only, e.g. "****0180"; "" when unset
     "reason": str,       # "" when ok, else a short machine-stable slug
     "detail": str,       # human text, truncated to 200 chars
   }
   ```
3. `reason` slugs, decided here so the caller can branch:
   - `"missing_credentials"` — app key, secret, or CANO absent after
     `load_kis_keys(mode)`.
   - `"auth_failed"` — token issuance raised (HTTP non-200 from `/oauth2/tokenP`).
   - `"rate_limited"` — the failure text contains `초당 거래건수`.
   - `"network"` — a `requests.RequestException` escaped.
   - `"rejected"` — a balance call returned `None` for any other reason.
   - `"unknown"` — anything else.
4. Wrap the whole body in try/except so the process **always** emits valid JSON
   and never a traceback on stdout — a traceback would break the caller's parse.
   Log the traceback to stderr instead.
5. Never place `KIS_APP_KEY`, `KIS_APP_SECRET`, or the full `KIS_CANO` in the
   output. Mask CANO as `"****" + cano[-4:]`.
6. Create `tests/test_kis_probe.py` (pytest) with `KisClient` monkeypatched — no
   test performs a real network call.

## Deliverables
- `src/broker/probe.py`
- `tests/test_kis_probe.py`

## Verify
- `.venv/bin/pytest tests/test_kis_probe.py -q` passes with at least:
  - normal: a stubbed client returning a `Balance` yields `ok is True`,
    `reason == ""`, and `cano_masked` ending in the last 4 digits.
  - normal: `json.dumps(probe())` round-trips through `json.loads` — the output
    is always valid JSON.
  - error: `load_kis_keys` leaving `KIS_APP_KEY` empty yields
    `ok is False` and `reason == "missing_credentials"`.
  - error: a stubbed `get_balance` raising `requests.RequestException` yields
    `reason == "network"` and `ok is False`.
  - error: a stubbed client whose token issuance raises `RuntimeError("KIS token 발급 실패: HTTP 403")`
    yields `reason == "auth_failed"`.
  - boundary: a stubbed `get_balance` returning `None` yields
    `reason == "rejected"` (not `"unknown"`).
  - boundary: a `detail` longer than 200 characters is truncated to exactly 200.
  - boundary: assert the serialized JSON of a run with
    `KIS_APP_SECRET="SUPERSECRET"` **does not contain** `"SUPERSECRET"` and does
    not contain the full CANO.

## Out of scope
- The TypeScript side that invokes this (task 11 owns the `kis-api` check).
