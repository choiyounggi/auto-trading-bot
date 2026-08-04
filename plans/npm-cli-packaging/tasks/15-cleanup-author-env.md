# Task 15: remove author-machine coupling

## Objective
No file outside `plans/` references `/Users/choeyeong-gi`, the committed
author-specific plists are gone (task 07 renders them at runtime instead), and
`pyproject.toml` describes what this package actually is.

## Wiki pages (read these first, only these)
- None. `[no-wiki]` — deletion and metadata correction; the replacement
  mechanism was decided in task 07.

## Inputs
- `plists/` — 8 committed plists: 3 with a `__PROJECT_DIR__` placeholder,
  5 with hardcoded `/Users/choeyeong-gi` paths and `com.choeyeonggi.*` labels.
- `scripts/install_macbook_home.sh` — author-only SSH deploy, hardcodes
  `REMOTE_DIR=/Users/choeyeong-gi/stock-trader`.
- `scripts/healthcheck.sh`, `scripts/export_research_data.py`,
  `scripts/test_kis_balance.py`, `scripts/setup.sh` — `~/stock-trader` and
  `~/Desktop/stock/10-tasks.md` path references in docstrings/comments.
- `cli/launchd.ts` from task 07 — the runtime renderer that replaces `plists/`.
- Decisions that bind you: D16 (delete the deploy script), D17 (pyproject).

## Steps
1. Delete the entire `plists/` directory. Task 07's `renderPlist` supersedes it;
   leaving both invites someone to edit the dead copy.
2. Delete `scripts/install_macbook_home.sh`.
3. Edit `pyproject.toml`:
   - `name = "kis-trader"`
   - `description = "LLM 기반 한국투자증권(KIS) 자동매매 엔진"` — the current
     text claims "키움 MCP", which is wrong on both counts.
   - Remove `"mcp>=0.9"` from `dependencies`. Verify first with
     `grep -rn "import mcp\|from mcp" src/ scripts/ tests/` returning 0 hits.
   - Leave `version`, `requires-python`, every other dependency, and all
     `[tool.*]` sections untouched.
4. Replace `~/stock-trader` in the four scripts' comments/docstrings with a
   path-neutral phrasing (e.g. "from the project root"). Replace the
   `~/Desktop/stock/10-tasks.md` pointer at the end of `scripts/setup.sh` with
   `"다음 단계: kis-trader init"`. Change no executable logic in any of them.
5. Do not touch `src/broker/kis_client.py`'s `~/.kis-token-{mode}.json` cache
   path — it is home-relative, already portable, and moving it would invalidate
   the author's live token cache mid-flight.

## Deliverables
- `plists/` (deleted), `scripts/install_macbook_home.sh` (deleted)
- `pyproject.toml` (modified)
- the four scripts' comment lines (modified)

## Verify
- `grep -rn "choeyeong-gi" . --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=plans --exclude-dir=dist --exclude-dir=dist-test`
  returns **0 hits**.
- `grep -rn "choeyeonggi" . --exclude-dir=.git --exclude-dir=plans` returns 0 hits.
- `grep -rn "import mcp\|from mcp" src/ scripts/ tests/` returns 0 hits
  (the evidence that dropping the dependency is safe).
- `test ! -d plists && test ! -f scripts/install_macbook_home.sh && echo CLEAN`
  prints `CLEAN`.
- `.venv/bin/pytest -q` still passes — nothing executable changed.
- `.venv/bin/python -c "import tomllib,pathlib; d=tomllib.loads(pathlib.Path('pyproject.toml').read_text()); assert d['project']['name']=='kis-trader'; assert not any('mcp' in x for x in d['project']['dependencies']); print('pyproject OK')"`
  prints `pyproject OK`.

## Out of scope
- `README.md` (task 16).
- Uninstalling the author's currently-loaded LaunchAgents from their machine —
  deleting repo files must not touch a running system. Note in the task report
  that the author should run `kis-trader install-jobs` to migrate to the new
  labels, and `launchctl bootout` the old `com.choeyeonggi.*` ones themselves.
