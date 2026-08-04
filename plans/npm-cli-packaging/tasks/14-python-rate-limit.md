# Task 14: fix the KIS rate-limit failures in `kis_client.py`

## Objective
A token issuance no longer collides with the request that triggered it, and a
transient "초당 거래건수 초과" on `get_balance()` is retried instead of killing
the day's entry run.

## Wiki pages (read these first, only these)
- wiki/backend/common/reliability/timeouts-and-retries.md — use for: rule 4
  (2–3 attempts, capped exponential backoff with **full jitter**:
  `sleep = random(0, min(cap, base × 2^attempt))`), the failure-type table's
  **429 row** (a throttle response is retried after backoff), and the
  "Retries exist at several layers" edge case — retry at one layer only.

## Inputs
- `src/broker/kis_client.py`:
  - `_throttle()` at line 223 and `_headers()` at line 233 — `_headers` calls
    `self._throttle()` and *then* `self._get_token()`, so the token POST issued
    inside `_get_token` is neither spaced nor counted.
  - `_get_token()` at line 188 — issues `POST /oauth2/tokenP`.
  - `get_balance()` at line 248 — returns `None` on any `rt_cd != "0"`.
  - `get_quote()` at lines 415–420 — the existing, correct retry this fix mirrors.
- Evidence the fix targets (from `data/logs/orchestrator.log` on the author's
  machine): `2026-08-04 09:05:00,354 토큰 발급 시도` → `,495 발급 완료` →
  `,543 get_balance 실패: 초당 거래건수 초과` — two requests inside one second.
- Decisions that bind you: D13.

## Steps
1. **Fix the token/throttle ordering.** In `_headers()`, resolve the token
   *before* stamping the throttle, and make the token POST itself throttled:
   - Move `token = self._get_token()` to the first statement of `_headers()`.
   - Inside `_get_token()`, call `self._throttle()` immediately before the
     `requests.post(...)` so the token request is spaced like any other and
     updates `_last_request_at`.
   - Keep the existing `self._throttle()` call in `_headers()` after the token
     is resolved, so the API call that follows is spaced from the token POST.
   - Net effect: on a token-issuing run the two requests are ≥
     `_min_request_interval` apart instead of ~150 ms apart.
2. **Add the retry to `get_balance()`.** Introduce a module-level helper so the
   policy exists once:
   ```python
   RATE_LIMIT_MARKER = "초당 거래건수"
   RATE_LIMIT_MAX_ATTEMPTS = 3   # total attempts, per the wiki's 2-3 budget
   RATE_LIMIT_BASE_SEC = 0.5
   RATE_LIMIT_CAP_SEC = 4.0

   def _rate_limit_backoff(attempt: int, rand=random.random) -> float:
       """Full jitter: random(0, min(cap, base * 2**attempt))."""
   ```
   `get_balance()` gains an internal attempt loop: when `rt_cd != "0"` and
   `RATE_LIMIT_MARKER` is in `msg1`, sleep `_rate_limit_backoff(attempt)` and
   retry, up to `RATE_LIMIT_MAX_ATTEMPTS` total. Any other `rt_cd != "0"`
   returns `None` immediately (the wiki's table: a non-throttle rejection is
   not retried). A `requests.RequestException` also returns `None` as today —
   changing network-error behaviour is out of scope.
   Accept `sleep=time.sleep` and `rand=random.random` as keyword parameters so
   the tests never actually sleep.
3. Do **not** add a retry in `orchestrator/__main__.py`. Retry lives at one
   layer only (wiki edge case); the orchestrator keeps its single
   `if balance is None: send_critical(...)`.
4. Create `tests/test_kis_rate_limit.py`.

## Deliverables
- `src/broker/kis_client.py` (modified)
- `tests/test_kis_rate_limit.py` (new)

## Verify
- `.venv/bin/pytest tests/test_kis_rate_limit.py -q` passes with at least:
  - normal: a stubbed transport returning throttle-then-success yields a
    `Balance` (not `None`) and the transport was called exactly twice.
  - normal: a first-try success calls the transport exactly once and never sleeps.
  - error: throttle on **every** attempt returns `None` and the transport was
    called exactly `RATE_LIMIT_MAX_ATTEMPTS` times — the budget is capped, not
    unbounded.
  - error: `rt_cd != "0"` with an unrelated `msg1` (`"해당 서비스를 찾을수 없습니다"`)
    returns `None` after exactly **one** call — non-throttle failures are not retried.
  - boundary: `_rate_limit_backoff(0, rand=lambda: 1.0) == 0.5` and
    `_rate_limit_backoff(0, rand=lambda: 0.0) == 0.0` — full jitter spans the
    whole interval including zero.
  - boundary: `_rate_limit_backoff(10, rand=lambda: 1.0) == 4.0` — the cap holds.
  - regression (write this one first and watch it fail before the fix):
    a `KisClient` whose token cache is empty, driven through one `get_balance`,
    records the token POST and the balance GET at least
    `_min_request_interval` apart — assert on an injected monotonic clock, not
    on wall time.
- `.venv/bin/pytest -q` — the whole existing suite still passes.

## Out of scope
- `get_overseas_balance`, `get_deposit`, and the order-submission paths. They
  share the defect but each needs its own regression test; list them as
  follow-ups in the task report rather than widening this change.
