# Task 02: declare the signal bot's Python dependencies

## Objective
`pyproject.toml` lists the six libraries the vendored signal code imports, so a
fresh `pip install -e ".[dev]"` produces a working signal pipeline.

## Wiki pages (read these first, only these)
- wiki/security/dependencies/supply-chain.md — use for: rule 2 (add-vs-write:
  these are protocol clients and parsers, not utilities to hand-roll), rule 3
  (verify the exact package name against the project's official docs — typosquats
  sit one character away), and rule 5 (install scripts are code execution).

## Inputs
- `~/stock-signal-bot/pyproject.toml` — the source declaration
- This repo's `pyproject.toml` — already has `pyyaml`, `requests`,
  `python-dotenv` among its dependencies
- Decisions that bind you: D10 (the six additions), D11 (keep `python-dotenv`).

## Steps
1. Add to `[project].dependencies`, preserving the existing entries and their
   order, appending these six:
   `"pykrx>=1.0.45"`, `"pandas>=2.0"`, `"numpy>=1.24"`,
   `"beautifulsoup4>=4.12"`, `"lxml>=5.0"`, `"yfinance>=0.2"`
2. Do **not** remove `python-dotenv` (D11) and do not touch `version`,
   `requires-python`, `name`, `description`, or any `[tool.*]` section.
3. Before adding, confirm each name resolves on PyPI — `pip index versions <name>`
   or `pip download --no-deps -d /dev/null <name>==<version>`. Record what you ran
   and its result; rule 3 exists because a wrong name is a supply-chain hole.
4. Reinstall into the repo venv: `.venv/bin/pip install -e ".[dev]" -q`.
5. **`tests/test_repo_hygiene.py::test_pyproject_leaves_everything_else_untouched`
   pins the exact dependency list and will fail — that is the guard working, not a
   bug.** Update *only* that one `assert project["dependencies"] == [...]` list to
   include the six new entries in the order you appended them. Preserve the
   assertion's purpose: it must still be an exact-list comparison, so a *seventh*
   unintended dependency still fails it. Do not touch any other assertion in that
   file, and do not relax `==` into a subset check.
   (Plan repair, recorded during execution: this task's original scope forbade
   touching `tests/`, which made its own Verify unsatisfiable.)

## Deliverables
- `pyproject.toml` (modified)
- `tests/test_repo_hygiene.py` (modified — one assertion, see step 5)

## Verify
- `.venv/bin/python -c "import pykrx, pandas, numpy, bs4, lxml, yfinance; print('ok')"`
  prints `ok`.
- `.venv/bin/python -c "import tomllib,pathlib; d=tomllib.loads(pathlib.Path('pyproject.toml').read_text()); deps=d['project']['dependencies']; req={'pykrx','pandas','numpy','beautifulsoup4','lxml','yfinance'}; got={x.split('>')[0].split('=')[0].strip() for x in deps}; assert req<=got, req-got; assert d['project']['name']=='kis-trader'; assert d['project']['version']=='0.0.1'; print('pyproject ok')"`
  prints `pyproject ok`.
- `.venv/bin/pytest -q` — the existing suite still passes (a dependency install
  must not break it).
- Paste the PyPI name-verification output from step 3.

## Out of scope
- Installing these on the *user's* machine at `init` time — `cli/bootstrap.ts`
  already runs `pip install -e ".[dev]"` and needs no change.
- npm-side dependencies — the zero-runtime-dependency rule is unaffected.
