# Task 10: bring the signal bot's config files into the package

## Objective
`config/` holds the three tuning files the signal engine reads, and they ship in
the npm tarball alongside `trading_rules.yaml`.

## Wiki pages (read these first, only these)
- wiki/infrastructure/config/environment-config.md — use for: rule 1 (one
  artifact ships everywhere; only configuration differs) and rule 4 (keep one
  inventory of config).

## Inputs
- `~/stock-signal-bot/config/thresholds.yaml` (44 lines)
- `~/stock-signal-bot/config/watchlist.txt` (38 lines)
- `~/stock-signal-bot/config/overseas_watchlist.yaml` (277 lines)
- This repo's `config/` already contains `trading_rules.yaml` — **no name
  collision**, and `package.json`'s `files` array already includes `config/`.
- Decisions that bind you: D13 (do not copy `*.bak*`).

## Steps
1. Copy the three files into this repo's `config/`, byte-for-byte. Do **not**
   copy `thresholds.yaml.bak-mktrisk` or `overseas_watchlist.yaml.bak-5tickers`.
2. Do not edit their contents. These are the author's tuned thresholds and
   watchlists; changing a number here silently changes what the bot trades.
3. Do **not** inspect or edit `src/signal/` — task 01 is creating that tree
   concurrently and owns every path expression inside it, including the ones that
   locate these three files. This task only places the files where task 01's code
   will look for them: `<repo>/config/`.

## Deliverables
- `config/thresholds.yaml`, `config/watchlist.txt`,
  `config/overseas_watchlist.yaml` (new)

## Verify
- `wc -l config/thresholds.yaml config/watchlist.txt config/overseas_watchlist.yaml`
  reports 44 / 38 / 277 — identical to the source, proving a faithful copy.
- `diff ~/stock-signal-bot/config/thresholds.yaml config/thresholds.yaml` is
  empty; same for the other two.
- `.venv/bin/python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('config/thresholds.yaml').read_text()); yaml.safe_load(pathlib.Path('config/overseas_watchlist.yaml').read_text()); print('yaml ok')"`
  prints `yaml ok`.
- `find config -name "*.bak*" | wc -l` is 0.
- `npm pack --dry-run 2>&1 | grep -c "config/thresholds.yaml"` is 1 — it ships.

## Out of scope
- `src/signal/**` entirely — task 01 owns it and runs in parallel with this task.
- `config/trading_rules.yaml` — the trader's, untouched.
- Any behaviour that reads these files.
