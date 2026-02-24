# CLAUDE.md

This file documents how to work on BacktesterV3 from the coding agent workflow.

## Project Overview

BacktesterV3 is a systematic backtesting platform with:
- QuantConnect LEAN (Docker) as the execution engine
- NiceGUI as the web shell
- Parameter sweep with visual dimension editor and sequential execution
- Multi-run comparison with trader score ranking and equity curve overlay

Core strategies:
- Common Breakout
- Episodic Pivot

## Commands

```bash
# install
pip install -e ".[dev]"

# run NiceGUI
python -m backtester

# lint
ruff check src tests
ruff format src tests

# tests
pytest
pytest tests/gui/test_config_service.py -k yaml
```

## Architecture

The engine is a 5-stage pipeline in `src/backtester/engine/runner.py`:

```text
RunConfig
  -> [1] Convert Data
  -> [2] Run LEAN
  -> [3] Parse Results
  -> [4] Match Trades
  -> [5] Compute Metrics
  -> RunResult
```

### Key boundaries

- `src/backtester/config.py`
  - Single source of truth for `RunConfig` and `SweepConfig`.
  - Frozen Pydantic models.
- `src/backtester/engine/runner.py`
  - Only GUI-to-engine entrypoint.
  - Returns `RunResult`.
- `src/backtester/engine/algorithm/QM_MVP.py`
  - Runs inside LEAN container.
- `src/backtester/sweep/grid.py`
  - Dotted-path override and cartesian grid expansion.
- `src/backtester/experiment.py`
  - Stable SHA-256 experiment id from config (excluding runner/output/run_name).

## GUI (Phase 2 + Phase 3)

All 4 tabs are functional:
- Configure: config editor, save/load, start single run
- Sweep: dimension editor, combo counter, sequential sweep execution
- Compare: sortable metrics table, trader score ranking, equity curve overlay, sweep group filter
- Results: KPI cards, equity curve, trade list, stage timeline

### GUI module map

```text
src/backtester/gui/
- app.py                          # NiceGUI app shell, routing, nav, launch()
- state.py                        # AppState + StateDB + JobState + RunSummary (incl sweep_group_id)
- pages/
  - configure_page.py             # config editor, save/load, start run
  - results_page.py               # KPI cards, equity curve, trades, stage timeline
  - sweep_page.py                 # dimension editor, combo counter, run sweep
  - compare_page.py               # ranked table, equity overlay, sweep group filter
- components/
  - config_form.py                # curated form + YAML toggle editor
  - progress.py                   # 5-stage pipeline progress component
  - metrics_cards.py              # KPI card grid
- services/
  - config_service.py             # defaults, yaml round-trip, config CRUD
  - backtest_service.py           # async run + sweep orchestration + artifact loading
```

## State Model

Persistence:
- SQLite file: `outputs/gui_state.db`

Tables:
- `saved_configs`:
  - named YAML configs
  - experiment id
  - created/updated timestamps
- `runs`:
  - run metadata, metrics, stage summaries
  - unique by `run_dir`
  - optional `sweep_group_id` for grouping sweep runs
- `ui_prefs`:
  - selected run id and future UI preferences

In-memory state:
- active config being edited
- live job registry (`queued/running/ok/error/skipped`)
- live progress events per job

## Execution Model

`src/backtester/gui/services/backtest_service.py`:
- one heavy job at a time (single active job guard)
- async in-process orchestration using `asyncio.to_thread(run_pipeline, ...)`
- `start_run()` for single runs, `start_sweep()` for sequential sweep execution
- sweep runs are grouped by `sweep_group_id` for filtering in Compare tab
- progress callback maps stage updates into GUI job state
- final run summaries are persisted to SQLite

Progress stages shown in UI:
1. `convert`
2. `lean`
3. `parse`
4. `match`
5. `metrics`

## Default Config Behavior

`ConfigService.build_default_config()`:
- pre-fills `data.parquet_root` with:
  - `/media/hp14linux/Daten/Backtest_data/ohlcv_test_20_tickers/parquet/session=rth/timeframe=1m`
  - only when the path exists
- fallback when not found: `parquet_data`
- baseline period: `2020-01-01` to `2021-12-31`
- baseline tickers: `AAPL, TSLA, NVDA`

## LEAN Runner Note

`src/backtester/engine/lean_runner.py` normalizes `runner.algorithm_file` so both work:
- `QM_MVP.py`
- `algorithm/QM_MVP.py`

Resulting LEAN config path:
- `/workspace/src/backtester/engine/algorithm/QM_MVP.py`

## Run Artifacts

Each pipeline run writes under `output.dir`:
- `run_config.json`
- `run_manifest.json`
- `metrics_summary.json`
- `trades.json`
- `lean_runner.log` (if LEAN attempted)

## Troubleshooting

### NiceGUI or Pydantic import errors

Install deps first:
```bash
pip install -e ".[dev]"
```

### Docker unavailable

- Configure page shows a warning badge.
- In `runner.mode=auto`, runs can end as skipped when Docker is unavailable.

### No results in Results tab

- Check `outputs/gui_state.db` has run records.
- Verify `run_manifest.json` and `metrics_summary.json` exist in run directory.

### LEAN algorithm not found

- Verify `runner.algorithm_file` is either `QM_MVP.py` or `algorithm/QM_MVP.py`.

## Test Coverage

- `tests/gui/test_state.py`
  - SQLite CRUD, in-memory job registry, sweep group persistence and queries
- `tests/gui/test_config_service.py`
  - default config, YAML round-trip, validation failure path
- `tests/gui/test_backtest_service.py`
  - async run lifecycle, single-job guard, error propagation, sweep execution
- `tests/engine/test_lean_runner_algorithm_path.py`
  - algorithm path normalization regression coverage
- `tests/test_comparison.py`
  - trader score computation, clamping, zero-trade penalty, run ranking

## Comparison & Trader Score

`src/backtester/analysis/comparison.py`:
- `compute_trader_score(metrics)` — weighted composite (PF 30%, win rate 20%, Sharpe 25%, drawdown 15%, trade count 10%)
- `rank_runs(runs)` — sorted by score descending
- Used by Compare page for table sorting and default equity overlay selection
