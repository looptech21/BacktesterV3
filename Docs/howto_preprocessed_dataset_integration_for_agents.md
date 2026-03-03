# Agent Runbook: Integrating the Frozen Preprocessed Dataset into BacktesterV3

> Mirror policy
> - Canonical file: `/mnt/Daten/Code/BacktesterV3/Docs/howto_preprocessed_dataset_integration_for_agents.md`
> - Mirror file: `/mnt/Daten/Code/OHLCV_preprocessor/docs/howto_backtesterv3_consumption_for_agents.md`
> - Update canonical first, then sync mirror in the same change.

## 1) Purpose and Scope

This runbook is the operational guide for Codex/Claude to integrate and use the frozen preprocessed dataset with BacktesterV3.

It defines:
- exact dataset path and supported layouts
- required BacktesterV3 config for correct consumption
- smoke-test commands
- expected outputs
- limitations and troubleshooting

This is documentation finalization for existing behavior. No code changes are required to follow this guide.

## 2) Allowed Dataset (Frozen Production Set)

Use this frozen dataset root:

- `/mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d`

Primary integration roots (symbol-year layout):

- `/mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d/bars/5m`
- `/mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d/bars/15m`
- `/mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d/bars/1h`
- `/mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d/bars/1d`

Dataset characteristics:
- split-adjusted already (backward split adjustment baked in)
- symbol-year filtered universe (same-year tradability profile)
- full-session bars in source data (premarket + RTH + postmarket)

## 3) Dataset Input Contract in BacktesterV3 Converter

BacktesterV3 production integration uses one source layout:

1. `symbol_year`
- files like `symbol=<SYM>/year=<YYYY>.parquet`

Use timeframe roots from the frozen dataset:
- `/mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d/bars/5m`
- `/mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d/bars/15m`
- `/mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d/bars/1h`
- `/mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d/bars/1d`

Notes:
- `bars/5m` is the default for integration smoke.
- `bars/1d` is valid for charting/context, but not for LEAN minute conversion runs.

## 4) Required BacktesterV3 Config Guidance

Set these values explicitly:

- `data.parquet_root`: one timeframe root, usually `.../bars/5m`
- `data.source_layout`: `symbol_year`
- `data.lean_feed_scope`: `full` (default) or `rth`
- `data.split_adjustment_mode`: `none`
- `data.timezone`: `America/New_York`
- `data.rth_start`: `09:30`
- `data.rth_end`: `16:00`
- `execution.order_session_scope`: `rth_only` (default policy)
- `setups.episodic_pivot.premarket_gate_source_mode`: `precomputed_preferred` (recommended)
- `runner.mode`: `auto` for full pipeline smoke
- `runner.mode`: `dotnet` only for convert-only smoke (LEAN stage is skipped in V3)
- `data.lean_data_root`: dedicated path per source/timeframe

Important:
- Do not enable split adjustment in BacktesterV3 when using this dataset, otherwise you risk double-adjustment.

### 4.1 EP Premarket Gate Source Modes

Use `setups.episodic_pivot.premarket_gate_source_mode` to control EP confirmation source:

- `precomputed_preferred` (default): use `features_premarket` first, fallback to runtime proxy only when precomputed row/fields are missing.
- `require_precomputed`: never fallback; reject EP activation when precomputed row is missing/incomplete.
- `proxy_only`: always use runtime proxy; ignore precomputed EP row for activation decisions.

Recommendation for frozen dataset runs: keep `precomputed_preferred` unless you explicitly want strict fail-closed behavior (`require_precomputed`) or debugging parity (`proxy_only`).

## 5) Pre-Flight Checks

### 5.1 Path and file-shape checks

```bash
DATA_ROOT=/mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d

[ -d "$DATA_ROOT" ] && echo "dataset_root_ok" || echo "dataset_root_missing"
[ -d "$DATA_ROOT/bars/5m" ] && echo "bars_5m_ok" || echo "bars_5m_missing"

find "$DATA_ROOT/bars/5m" -maxdepth 2 -type f -name 'year=*.parquet' | head
```

### 5.2 Docker checks (required for full pipeline)

```bash
docker ps
docker run --rm hello-world
```

If Docker checks fail, do not run full LEAN smoke yet.

## 6) Minimal Working Config (Copy/Paste)

Use this as a runtime config object for smoke execution:

```json
{
  "run_name": "integration_smoke_preprocessed_5m",
  "data": {
    "parquet_root": "/mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d/bars/5m",
    "lean_data_root": "/tmp/backtesterv3_integration_smoke_preprocessed/lean_data",
    "cache_root": "/tmp/backtesterv3_integration_smoke_preprocessed/cache",
    "timezone": "America/New_York",
    "rth_start": "09:30",
    "rth_end": "16:00",
    "source_layout": "symbol_year",
    "lean_feed_scope": "full",
    "split_adjustment_mode": "none"
  },
  "universe": {
    "mode": "custom_list",
    "tickers": ["AAPL", "TSLA", "NVDA"]
  },
  "period": {
    "start_date": "2024-05-01",
    "end_date": "2024-05-31"
  },
  "execution": {
    "order_session_scope": "rth_only"
  },
  "runner": {
    "mode": "auto"
  },
  "output": {
    "dir": "/tmp/backtesterv3_integration_smoke_preprocessed/runs",
    "generate_charts": false,
    "generate_html_report": false
  }
}
```

## 7) Integration Procedure (CLI-Oriented)

### 7.1 Convert-only smoke (safe first pass)

```bash
cd /mnt/Daten/Code/BacktesterV3
source venv/bin/activate

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ./venv/bin/python - <<'PY'
import json
from pathlib import Path
from backtester.config import RunConfig
from backtester.engine.runner import run_pipeline

cfg = RunConfig.model_validate({
    "run_name": "integration_smoke_preprocessed_5m_convert_only",
    "data": {
        "parquet_root": "/mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d/bars/5m",
        "lean_data_root": "/tmp/backtesterv3_integration_smoke_preprocessed/lean_data",
        "cache_root": "/tmp/backtesterv3_integration_smoke_preprocessed/cache",
        "timezone": "America/New_York",
        "rth_start": "09:30",
        "rth_end": "16:00",
        "split_adjustment_mode": "none"
    },
    "universe": {"mode": "custom_list", "tickers": ["AAPL", "TSLA", "NVDA"]},
    "period": {"start_date": "2024-05-01", "end_date": "2024-05-31"},
    "runner": {"mode": "dotnet"},
    "output": {
        "dir": "/tmp/backtesterv3_integration_smoke_preprocessed/runs",
        "generate_charts": False,
        "generate_html_report": False
    }
})

res = run_pipeline(cfg, repo_root=Path('/mnt/Daten/Code/BacktesterV3'), dry_run=False)
print(json.dumps({
    "status": res.status,
    "error": res.error,
    "run_dir": str(res.run_dir),
    "stages": [{"stage": s.stage, "status": s.status, "detail": s.detail, "error": s.error} for s in res.stages]
}, indent=2))
PY
```

Expected:
- `convert`: `ok`
- `lean`: `skipped` (dotnet unsupported in V3)

### 7.2 Full pipeline smoke (with Docker)

Use the same script but set:

- `"runner": {"mode": "auto"}`

Expected stage statuses:
- `convert`: `ok`
- `lean`: `ok` (docker)
- `parse`: `ok`
- `match`: `ok`
- `metrics`: `ok`

Note:
- `num_trades` may be `0` in smoke. This is acceptable for integration verification.
- BacktesterV3 remaps `data.parquet_root` to a container-visible path automatically for LEAN (`/Lean/PreparedDataset/...`), so absolute host roots like `/mnt/...` are supported.

## 8) Expected Outputs and Success Criteria

### 8.1 Converted LEAN data

Path pattern:

- `.../lean_data/equity/usa/minute/<symbol>/<yyyymmdd>_trade.zip`

Sanity check:

```bash
find /tmp/backtesterv3_integration_smoke_preprocessed/lean_data/equity/usa/minute -type f -name '*.zip' | wc -l
find /tmp/backtesterv3_integration_smoke_preprocessed/lean_data/equity/usa/minute -type f -name '*.zip' | head
```

### 8.2 Run artifacts

Each run writes a run directory under:

- `/tmp/backtesterv3_integration_smoke_preprocessed/runs/`

Inspect manifest:

```bash
RUN_DIR=$(ls -1dt /tmp/backtesterv3_integration_smoke_preprocessed/runs/integration_smoke_preprocessed_5m_* | head -n 1)
cat "$RUN_DIR/run_manifest.json"
```

Success criteria:
- pipeline status is `ok` for full smoke
- stage details show expected converter counts
- no LEAN runner error

## 9) Troubleshooting

### 9.1 Docker socket permission denied

Symptom:
- LEAN stage error with `/var/run/docker.sock` permission denied.

Actions:
- verify local docker access with `docker ps` and `docker run --rm hello-world`
- if running in sandboxed agent context, rerun with elevated execution permission

### 9.2 No files converted

Common causes:
- wrong `data.parquet_root`
- period outside available years
- ticker list not present in filtered symbol-year universe

Checks:

```bash
find /mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d/bars/5m -maxdepth 2 -type f -name 'year=*.parquet' | head
```

### 9.3 Accidental double split-adjustment

Symptom:
- distorted prices from additional backward adjustment.

Fix:
- enforce `data.split_adjustment_mode: none` for frozen pre-adjusted dataset.

### 9.4 Empty trades in smoke

This is not automatically a failure.
- integration smoke validates data wiring and execution path
- strategy/signal density can produce zero fills for short windows

### 9.5 EP precomputed gate source not used when expected

Symptoms:
- `lean_runtime_debug.json` shows `ep_gate_precomputed_* = 0` while you expected precomputed usage.

Checks:
- verify `setups.episodic_pivot.premarket_gate_source_mode` is not `proxy_only`
- verify the period actually generates EP setups (`setups_generated > 0`)
- verify dataset has `features_premarket/symbol=<SYM>/year=<YYYY>.parquet` for your symbols/years
- inspect `setup_trace.json` and filter `evidence.gate_source` to confirm source path (`precomputed` vs `proxy_fallback`)

## 10) Limitations and Caveats (Critical)

1. Feed-scope behavior in converter:
- `data.lean_feed_scope = full` keeps premarket + RTH + postmarket bars in LEAN zips.
- `data.lean_feed_scope = rth` clips to the configured RTH window.

2. 5m source into minute-resolution algorithm:
- LEAN stream is minute-resolution, but source timestamps are 5m bars.
- This yields sparse minute timelines (bars at 5-minute marks).

3. Strategy calibration risk:
- entry ladder/opening-volume behavior tuned for dense 1m can differ on 5m-derived minute stream.

4. Daily root (`bars/1d`) limitation for LEAN conversion:
- `bars/1d` is rejected by converter for minute LEAN data creation.

5. Filtered universe expectation:
- symbol-year omissions are expected because the dataset is prefiltered.
- missing symbol-year is not necessarily a data corruption issue.

6. Dotnet runner mode:
- `runner.mode = dotnet` is convert-only today (`lean` stage is intentionally skipped in V3).

7. GUI support status:
- GUI defaults/discovery and trade inspection now support `bars/<tf>/symbol=<SYM>/year=<YYYY>.parquet` roots.
- session filtering in trade inspection is synthetic (`full/rth/premarket/postmarket`) over intraday flags.

## 11) Agent Operating Rules (Codex/Claude)

Before each integration run, always print/verify:
- effective `data.parquet_root`
- timeframe implied by root (for example `bars/5m`)
- `data.split_adjustment_mode` (must be `none`)
- `period.start_date` and `period.end_date`
- `runner.mode`

Execution policy:
- always run a 3-symbol smoke (`AAPL, TSLA, NVDA`) before broader runs
- never mutate frozen dataset files under `/mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d`
- treat warnings and skips as actionable diagnostics

## 12) Documentation Verification Scenarios

1. Follow-the-doc reproducibility test:
- run exactly the smoke steps above
- confirm stage structure and status are as documented

2. Negative config test:
- set `data.split_adjustment_mode` to `splits_backward`
- verify behavior is flagged as wrong in review and corrected to `none`

3. Wrong-root test:
- point `data.parquet_root` to a non-matching path
- verify troubleshooting flow identifies root mismatch

4. GUI integration confirmation:
- verify GUI source discovery and trade inspection resolve symbol-year roots correctly
- verify manual pipeline config still matches GUI-selected roots/timeframe
