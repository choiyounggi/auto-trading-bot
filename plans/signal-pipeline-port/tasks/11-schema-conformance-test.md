# Task 11: prove the produced signal JSON matches the schema the trader validates

## Objective
A test builds a signal JSON through `dump_signals_json` and validates it against
this repo's own `schemas/signal-v1.json`, so the producer and the consumer cannot
drift apart silently.

## Wiki pages (read these first, only these)
- wiki/testing/quality/minimum-case-set.md — use for: rule 2 (assert an
  observable outcome), rule 4 (assert the error contract, not a bare "it throws").
- wiki/testing/quality/tests-that-cannot-fail.md — use for: judging whether this
  check can actually detect drift; a schema test that passes because the schema
  permits everything is worthless.

## Inputs
- `src/signal/data/dump_signals.py` from tasks 01+04 — `dump_signals_json(...)`
- `schemas/signal-v1.json` — already in this repo; it is what
  `src/orchestrator/signal_loader.py` validates incoming files against
- `src/orchestrator/signal_loader.py` — `load_signal(signal_dir, target_date,
  schema_path, max_age_min, name_suffix)`, the real consumer
- `jsonschema>=4.21` is already a declared dependency
- Decisions that bind you: D12 (contract surface only — no analyser tests).

## Steps
1. Create `tests/test_signal_schema_contract.py`.
2. Build a minimal but realistic payload and write it with `dump_signals_json`
   into a `tmp_path`, passing `out_dir` explicitly.
3. Validate the written file against `schemas/signal-v1.json` using
   `jsonschema.validate`.
4. **Then close the loop**: call the trader's own `load_signal` against that
   directory and assert it returns the parsed object rather than `None`. This is
   the assertion that actually matters — it exercises the real consumer, not a
   re-implementation of it. Pass `max_age_min` generously so freshness does not
   confound the schema check.
5. Add a negative control proving the check can fail: mutate the written JSON to
   violate the schema (drop a required key, or set `strategy_signals` to a
   non-array) and assert `jsonschema.ValidationError` is raised — asserting the
   exception type and that its message names the offending field.

## Deliverables
- `tests/test_signal_schema_contract.py` (new)

## Verify
- `.venv/bin/pytest tests/test_signal_schema_contract.py -q` passes with at least:
  - normal: a produced file validates against `schemas/signal-v1.json`.
  - normal: `load_signal` reads that same directory and returns a dict whose
    `strategy_signals` is a list — the producer→consumer round trip.
  - error: a file with a required key removed raises `ValidationError`; assert the
    type and that `str(err)` mentions the removed key.
  - error: `strategy_signals` set to a string instead of a list raises
    `ValidationError`.
  - boundary: an empty-but-valid payload (`buys: []`, `cautions: []`,
    `strategy_signals: []`) still validates and still round-trips through
    `load_signal` — an empty signal day is normal, not an error.
  - boundary: the negative control above is what proves this suite is not vacuous;
    state in the test's docstring that it exists for that reason.
- `.venv/bin/pytest -q` — whole suite green.

## Out of scope
- Changing `schemas/signal-v1.json`. If the produced JSON genuinely does not match
  it, that is a finding: report it with the exact mismatch rather than editing
  either side to force agreement — the schema is the trader's existing contract.
