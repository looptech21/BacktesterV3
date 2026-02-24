"""5-stage pipeline orchestrator.

Orchestrates: Convert Data → Run LEAN → Parse Results → Match Trades → Compute Metrics.
This is the single interface between the GUI and the engine.
"""

from __future__ import annotations

import json
import platform
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Literal

from backtester.config import RunConfig
from backtester.engine.data_converter import ConversionConfig, ConversionReport, convert_parquet_to_lean
from backtester.engine.lean_ingest import load_lean_fills
from backtester.engine.lean_runner import LeanResult, run_lean
from backtester.engine.trade_matcher import MatchingResult, match_trades_fifo
from backtester.analysis.metrics import compute_metrics_from_trades
from backtester.experiment import experiment_id
from backtester.io_utils import write_json


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageResult:
    stage: str
    status: Literal["ok", "skipped", "error"]
    duration_ms: int
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class RunResult:
    experiment_id: str
    run_name: str
    run_dir: Path
    config: RunConfig
    trades: list[dict[str, Any]]
    metrics: dict[str, Any]
    manifest: dict[str, Any]
    status: Literal["ok", "error", "skipped"]
    stages: list[StageResult] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# Progress callback protocol
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[str, str, float | None], None]
"""progress_callback(stage_name, message, fraction_or_none)"""


# ---------------------------------------------------------------------------
# Run directory management
# ---------------------------------------------------------------------------


def _build_run_dir(output_root: Path, run_name: str, exp_id: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = output_root / f"{run_name}_{ts}_{exp_id[:10]}"
    idx = 0
    while True:
        candidate = run_dir if idx == 0 else output_root / f"{run_name}_{ts}_{exp_id[:10]}_{idx}"
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            idx += 1


def _to_conversion_config(config: RunConfig, tickers: list[str]) -> ConversionConfig:
    return ConversionConfig(
        parquet_root=Path(config.data.parquet_root),
        lean_data_root=Path(config.data.lean_data_root),
        cache_root=Path(config.data.cache_root),
        tickers=tickers,
        start_date=config.period.start_date,
        end_date=config.period.end_date,
        timezone=config.data.timezone,
        rth_start=config.data.rth_start,
        rth_end=config.data.rth_end,
    )


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


def _stage_convert(
    config: RunConfig,
    tickers: list[str],
    run_dir: Path,
    *,
    progress: ProgressCallback | None = None,
    dry_run: bool = False,
) -> StageResult:
    """Stage 1: Convert parquet OHLCV data to LEAN minute format."""
    if not config.runner.auto_convert_data:
        return StageResult(stage="convert", status="skipped", duration_ms=0, detail={"reason": "auto_convert_data=false"})

    if dry_run:
        return StageResult(stage="convert", status="skipped", duration_ms=0, detail={"reason": "dry_run"})

    if progress:
        progress("convert", "Converting parquet data to LEAN format...", None)

    t0 = perf_counter()
    try:
        conv_config = _to_conversion_config(config, tickers)
        report = convert_parquet_to_lean(conv_config)
        duration = int((perf_counter() - t0) * 1000)
        return StageResult(
            stage="convert",
            status="ok",
            duration_ms=duration,
            detail={
                "converted_files": report.converted_files,
                "skipped_files": report.skipped_files,
                "rows_written": report.rows_written,
            },
        )
    except Exception as exc:
        duration = int((perf_counter() - t0) * 1000)
        return StageResult(stage="convert", status="error", duration_ms=duration, error=str(exc))


def _stage_lean(
    config: RunConfig,
    run_dir: Path,
    exp_id: str,
    repo_root: Path,
    *,
    progress: ProgressCallback | None = None,
    dry_run: bool = False,
) -> tuple[StageResult, LeanResult | None]:
    """Stage 2: Execute LEAN via Docker."""
    if progress:
        progress("lean", "Running LEAN engine...", None)

    t0 = perf_counter()
    try:
        result = run_lean(config, run_dir, exp_id, repo_root, dry_run=dry_run)
        duration = int((perf_counter() - t0) * 1000)

        if result.status in ("ok", "dry_run"):
            return StageResult(
                stage="lean",
                status="ok",
                duration_ms=duration,
                detail={"runner": result.runner, "returncode": result.returncode},
            ), result
        elif result.status == "skipped":
            return StageResult(
                stage="lean",
                status="skipped",
                duration_ms=duration,
                detail={"runner": result.runner, "reason": result.error or "no runner available"},
            ), result
        else:
            return StageResult(
                stage="lean",
                status="error",
                duration_ms=duration,
                error=result.error,
                detail={"runner": result.runner, "returncode": result.returncode},
            ), result
    except Exception as exc:
        duration = int((perf_counter() - t0) * 1000)
        return StageResult(stage="lean", status="error", duration_ms=duration, error=str(exc)), None


def _stage_parse(
    run_dir: Path,
    *,
    progress: ProgressCallback | None = None,
) -> tuple[StageResult, list[dict[str, Any]]]:
    """Stage 3: Parse LEAN output JSON into normalized fills."""
    if progress:
        progress("parse", "Parsing LEAN output...", None)

    t0 = perf_counter()
    try:
        fills = load_lean_fills(run_dir)
        duration = int((perf_counter() - t0) * 1000)
        return StageResult(
            stage="parse",
            status="ok",
            duration_ms=duration,
            detail={"num_fills": len(fills)},
        ), fills
    except Exception as exc:
        duration = int((perf_counter() - t0) * 1000)
        return StageResult(stage="parse", status="error", duration_ms=duration, error=str(exc)), []


def _stage_match(
    fills: list[dict[str, Any]],
    *,
    progress: ProgressCallback | None = None,
) -> tuple[StageResult, MatchingResult]:
    """Stage 4: FIFO fill matching into round-trip trades."""
    if progress:
        progress("match", "Matching fills into trades...", None)

    t0 = perf_counter()
    try:
        result = match_trades_fifo(fills)
        duration = int((perf_counter() - t0) * 1000)
        return StageResult(
            stage="match",
            status="ok",
            duration_ms=duration,
            detail={
                "num_fills": result.num_fills,
                "num_closed_trades": result.num_closed_trades,
                "num_open_lots_end": result.num_open_lots_end,
                "violations_count": len(result.violations),
            },
        ), result
    except Exception as exc:
        duration = int((perf_counter() - t0) * 1000)
        empty = MatchingResult(trades=[], num_fills=0, num_closed_trades=0, num_open_lots_end=0, violations=[])
        return StageResult(stage="match", status="error", duration_ms=duration, error=str(exc)), empty


def _stage_metrics(
    trades: list[dict[str, Any]],
    config: RunConfig,
    *,
    progress: ProgressCallback | None = None,
) -> tuple[StageResult, dict[str, Any]]:
    """Stage 5: Compute backtest metrics from matched trades."""
    if progress:
        progress("metrics", "Computing metrics...", None)

    t0 = perf_counter()
    try:
        metrics = compute_metrics_from_trades(trades, initial_cash=config.execution.initial_cash)
        duration = int((perf_counter() - t0) * 1000)
        return StageResult(
            stage="metrics",
            status="ok",
            duration_ms=duration,
            detail={"num_trades": len(trades)},
        ), metrics
    except Exception as exc:
        duration = int((perf_counter() - t0) * 1000)
        return StageResult(stage="metrics", status="error", duration_ms=duration, error=str(exc)), {}


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def _build_manifest(
    *,
    exp_id: str,
    config: RunConfig,
    run_dir: Path,
    stages: list[StageResult],
    trades: list[dict[str, Any]],
    metrics: dict[str, Any],
    tickers: list[str],
    status: str,
) -> dict[str, Any]:
    return {
        "experiment_id": exp_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "schema_version": config.schema_version,
        "run_name": config.run_name,
        "run_dir": str(run_dir),
        "status": status,
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
        },
        "parameters": config.model_dump(mode="json"),
        "universe": tickers,
        "stages": [
            {
                "stage": s.stage,
                "status": s.status,
                "duration_ms": s.duration_ms,
                "detail": s.detail,
                "error": s.error,
            }
            for s in stages
        ],
        "metrics_summary": metrics,
        "num_trades": len(trades),
    }


def run_pipeline(
    config: RunConfig,
    *,
    repo_root: Path | None = None,
    output_root: Path | None = None,
    progress_callback: ProgressCallback | None = None,
    dry_run: bool = False,
) -> RunResult:
    """Execute the full 5-stage backtesting pipeline.

    Stages:
        1. Convert Data — Parquet to LEAN format (incremental)
        2. Run LEAN — Docker execution
        3. Parse Results — Extract fills from LEAN output
        4. Match Trades — FIFO fill matching
        5. Compute Metrics — Win rate, PF, drawdown, etc.
    """
    if repo_root is None:
        repo_root = Path.cwd()

    if output_root is None:
        output_root = Path(config.output.dir).resolve()

    tickers = config.resolved_tickers()
    exp_id = experiment_id(config)
    run_dir = _build_run_dir(output_root, config.run_name, exp_id)

    # Save config to run directory
    config_dump = config.model_dump(mode="json")
    write_json(run_dir / "run_config.json", config_dump)

    stages: list[StageResult] = []

    # Stage 1: Convert Data
    stage1 = _stage_convert(config, tickers, run_dir, progress=progress_callback, dry_run=dry_run)
    stages.append(stage1)
    if stage1.status == "error":
        manifest = _build_manifest(
            exp_id=exp_id, config=config, run_dir=run_dir, stages=stages,
            trades=[], metrics={}, tickers=tickers, status="error",
        )
        write_json(run_dir / "run_manifest.json", manifest)
        return RunResult(
            experiment_id=exp_id, run_name=config.run_name, run_dir=run_dir,
            config=config, trades=[], metrics={}, manifest=manifest,
            status="error", stages=stages, error=f"Convert stage failed: {stage1.error}",
        )

    # Stage 2: Run LEAN
    stage2, lean_result = _stage_lean(config, run_dir, exp_id, repo_root, progress=progress_callback, dry_run=dry_run)
    stages.append(stage2)
    if stage2.status == "error":
        manifest = _build_manifest(
            exp_id=exp_id, config=config, run_dir=run_dir, stages=stages,
            trades=[], metrics={}, tickers=tickers, status="error",
        )
        write_json(run_dir / "run_manifest.json", manifest)
        return RunResult(
            experiment_id=exp_id, run_name=config.run_name, run_dir=run_dir,
            config=config, trades=[], metrics={}, manifest=manifest,
            status="error", stages=stages, error=f"LEAN stage failed: {stage2.error}",
        )
    if stage2.status == "skipped":
        manifest = _build_manifest(
            exp_id=exp_id, config=config, run_dir=run_dir, stages=stages,
            trades=[], metrics={}, tickers=tickers, status="skipped",
        )
        write_json(run_dir / "run_manifest.json", manifest)
        return RunResult(
            experiment_id=exp_id, run_name=config.run_name, run_dir=run_dir,
            config=config, trades=[], metrics={}, manifest=manifest,
            status="skipped", stages=stages, error=stage2.detail.get("reason", "LEAN skipped"),
        )

    # Stage 3: Parse Results
    stage3, fills = _stage_parse(run_dir, progress=progress_callback)
    stages.append(stage3)

    # Stage 4: Match Trades
    stage4, matching = _stage_match(fills, progress=progress_callback)
    stages.append(stage4)
    trades = matching.trades

    # Save trade artifacts
    write_json(run_dir / "lean_fills_normalized.json", fills)
    write_json(run_dir / "trades.json", trades)
    if matching.violations:
        write_json(run_dir / "matching_violations.json", matching.violations)

    # Stage 5: Compute Metrics
    stage5, metrics = _stage_metrics(trades, config, progress=progress_callback)
    stages.append(stage5)
    write_json(run_dir / "metrics_summary.json", metrics)

    # Build and save manifest
    overall_status: Literal["ok", "error", "skipped"] = "ok"
    if any(s.status == "error" for s in stages):
        overall_status = "error"

    manifest = _build_manifest(
        exp_id=exp_id, config=config, run_dir=run_dir, stages=stages,
        trades=trades, metrics=metrics, tickers=tickers, status=overall_status,
    )
    write_json(run_dir / "run_manifest.json", manifest)

    return RunResult(
        experiment_id=exp_id,
        run_name=config.run_name,
        run_dir=run_dir,
        config=config,
        trades=trades,
        metrics=metrics,
        manifest=manifest,
        status=overall_status,
        stages=stages,
    )
