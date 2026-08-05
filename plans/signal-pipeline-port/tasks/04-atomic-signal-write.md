# Task 04: write the signal JSON atomically, into the directory the trader reads

## Objective
`dump_signals_json` resolves its output directory from `KIS_TRADER_SIGNAL_DIR`
(the same resolver the trader reads through) and writes via a temp file plus
`os.replace`, so the trader can never parse a half-written signal file.

## Wiki pages (read these first, only these)
- wiki/backend/common/jobs/scheduled-job-overlap.md — use for: rule 1 (assume the
  scheduler starts overlapping runs — the 16:30 signal job can still be writing
  when the 16:45 trader job reads) and rule 4 (make the job's effect idempotent so
  a re-run reproduces state instead of doubling it).
- wiki/infrastructure/config/environment-config.md — use for: rule 2, the
  directory is a named config value read through one code path.

## Inputs
- `src/signal/data/dump_signals.py` from task 01 — `dump_signals_json(...)`, which
  today does `out_path.write_text(json.dumps(out, ...), encoding="utf-8")` and
  defaults `out_dir` to `Path(__file__).resolve().parents[2] / "data" / "signals"`
- `src/orchestrator/signal_loader.py` (already in this repo) — exports
  `resolve_signal_dir(env=None) -> Path` and `DEFAULT_SIGNAL_DIR`. **Reuse it;
  do not write a second resolver.**
- Decisions that bind you: D5 (same directory as the trader reads), D6 (atomic
  write via `os.replace`).

## Steps
1. In `dump_signals_json`, replace the `out_dir` default. Keep the explicit
   `out_dir` parameter working (callers and tests pass it); when it is `None`,
   resolve with `resolve_signal_dir()` imported from
   `src.orchestrator.signal_loader`. That function already honours
   `KIS_TRADER_SIGNAL_DIR`, expands `~`, and rejects a relative value.
2. Replace the write with an atomic sequence in the **same directory** as the
   destination (a cross-filesystem rename is not atomic):
   ```python
   tmp = out_path.with_suffix(out_path.suffix + ".tmp")
   tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
   os.replace(tmp, out_path)
   ```
   Remove the leftover `.tmp` in a `finally` if `os.replace` raises, so a failed
   run does not litter the directory the trader scans for freshness.
3. `mkdir(parents=True, exist_ok=True)` the destination before writing.
4. Change nothing about the JSON's **content** — the keys, ordering and values
   are the trader's contract and task 11 asserts them.
5. Create `tests/test_dump_signals_atomic.py`.

## Deliverables
- `src/signal/data/dump_signals.py` (modified)
- `tests/test_dump_signals_atomic.py` (new)

## Verify
- `.venv/bin/pytest tests/test_dump_signals_atomic.py -q` passes with at least:
  - normal: called with an explicit `out_dir` (a tmp_path) → the file exists at
    `<out_dir>/<YYYY-MM-DD>.json` and `json.loads` round-trips it.
  - normal: with `out_dir=None` and `KIS_TRADER_SIGNAL_DIR` set to a tmp_path →
    the file lands **there**, proving the trader's resolver is the one in use.
  - normal: no `.tmp` file remains after a successful write (list the directory
    and assert exactly one entry).
  - error: monkeypatch `os.replace` to raise → the exception surfaces **and** no
    `.tmp` file is left behind (assert the directory is empty).
  - error: `KIS_TRADER_SIGNAL_DIR` set to a relative path → the `ValueError` from
    `resolve_signal_dir` propagates; assert the type and that the message contains
    `"must be an absolute path"`.
  - boundary: the destination directory does not exist yet → it is created.
  - boundary: writing twice for the same date overwrites cleanly and the result is
    still valid JSON (the job is re-runnable — wiki rule 4).
  - boundary (the reason this task exists): assert the temp file's name differs
    from the final name, so a reader globbing `*.json` never sees the partial file
    — assert the temp path does **not** end in `.json`.
- `.venv/bin/pytest -q` — whole suite green.

## Out of scope
- The JSON's schema/content — task 11 asserts it against `schemas/signal-v1.json`.
- The launchd timeout/lock that bounds the run — task 07.
