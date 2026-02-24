from __future__ import annotations

import asyncio
import time
from pathlib import Path

import backtester.gui.services.backtest_service as backtest_service_module
from backtester.config import RunConfig
from backtester.engine.runner import RunResult, StageResult
from backtester.gui.services.backtest_service import BacktestService
from backtester.gui.state import init_app_state
from backtester.io_utils import write_json


def _sample_config(tmp_path: Path) -> RunConfig:
    return RunConfig.model_validate(
        {
            "run_name": "gui_test",
            "period": {"start_date": "2020-01-01", "end_date": "2020-01-31"},
            "data": {"parquet_root": str(tmp_path / "parquet")},
            "universe": {"mode": "custom_list", "tickers": ["AAPL"]},
            "output": {"dir": str(tmp_path / "outputs")},
        }
    )


def _make_success_result(config: RunConfig, output_root: Path) -> RunResult:
    run_dir = output_root / "run_success"
    run_dir.mkdir(parents=True, exist_ok=True)
    trades = [
        {
            "trade_id": "T0001",
            "symbol": "AAPL",
            "entry_fill_ts_utc": "2020-01-02T14:31:00+00:00",
            "exit_fill_ts_utc": "2020-01-03T15:00:00+00:00",
            "pnl_net": 10.5,
        }
    ]
    metrics = {"net_pnl": 10.5, "num_trades": 1, "win_rate": 1.0, "equity_curve": [10.5]}
    stage_dict = {"stage": "convert", "status": "ok", "duration_ms": 12, "detail": {}, "error": None}
    manifest = {
        "timestamp_utc": "2026-02-22T00:00:00+00:00",
        "stages": [stage_dict],
        "metrics_summary": metrics,
    }
    write_json(run_dir / "run_manifest.json", manifest)
    write_json(run_dir / "metrics_summary.json", metrics)
    write_json(run_dir / "trades.json", trades)
    write_json(run_dir / "run_config.json", config.model_dump(mode="json"))
    return RunResult(
        experiment_id="exp_test",
        run_name=config.run_name,
        run_dir=run_dir,
        config=config,
        trades=trades,
        metrics=metrics,
        manifest=manifest,
        status="ok",
        stages=[StageResult(stage="convert", status="ok", duration_ms=12)],
        error=None,
    )


def test_backtest_service_start_run_success_persists_run(tmp_path, monkeypatch):
    state = init_app_state(tmp_path / "outputs")
    service = BacktestService(state)
    config = _sample_config(tmp_path)

    def fake_run_pipeline(*args, **kwargs):  # type: ignore[no-untyped-def]
        progress_callback = kwargs.get("progress_callback")
        output_root = kwargs.get("output_root", tmp_path / "outputs")
        if progress_callback:
            progress_callback("convert", "Converting...", 0.2)
        return _make_success_result(config, Path(output_root))

    monkeypatch.setattr(backtest_service_module, "run_pipeline", fake_run_pipeline)

    async def scenario() -> None:
        job_id = service.start_run(config)
        for _ in range(200):
            job = service.get_job(job_id)
            if job is not None and job.status in {"ok", "error", "skipped"}:
                break
            await asyncio.sleep(0.01)
        job = service.get_job(job_id)
        assert job is not None
        assert job.status == "ok"
        assert job.run_id is not None
        runs = service.list_runs()
        assert len(runs) == 1
        artifacts = service.load_run_artifacts(Path(runs[0].run_dir))
        assert artifacts["metrics"]["net_pnl"] == 10.5

    asyncio.run(scenario())


def test_backtest_service_single_active_job_guard(tmp_path, monkeypatch):
    state = init_app_state(tmp_path / "outputs")
    service = BacktestService(state)
    config = _sample_config(tmp_path)

    def fake_run_pipeline(*args, **kwargs):  # type: ignore[no-untyped-def]
        time.sleep(0.2)
        output_root = kwargs.get("output_root", tmp_path / "outputs")
        return _make_success_result(config, Path(output_root))

    monkeypatch.setattr(backtest_service_module, "run_pipeline", fake_run_pipeline)

    async def scenario() -> None:
        first_job = service.start_run(config)
        try:
            service.start_run(config)
        except RuntimeError as exc:
            assert "already running" in str(exc).lower()
        else:
            raise AssertionError("Expected single-job guard to reject second job")

        for _ in range(200):
            first = service.get_job(first_job)
            if first is not None and first.status in {"ok", "error", "skipped"}:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())


def test_backtest_service_run_failure_sets_error_status(tmp_path, monkeypatch):
    state = init_app_state(tmp_path / "outputs")
    service = BacktestService(state)
    config = _sample_config(tmp_path)

    def fake_run_pipeline(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(backtest_service_module, "run_pipeline", fake_run_pipeline)

    async def scenario() -> None:
        job_id = service.start_run(config)
        for _ in range(200):
            job = service.get_job(job_id)
            if job is not None and job.status in {"ok", "error", "skipped"}:
                break
            await asyncio.sleep(0.01)
        job = service.get_job(job_id)
        assert job is not None
        assert job.status == "error"
        assert "synthetic failure" in (job.error or "")
        assert service.list_runs() == []

    asyncio.run(scenario())


def test_backtest_service_start_sweep_runs_all_configs(tmp_path, monkeypatch):
    state = init_app_state(tmp_path / "outputs")
    service = BacktestService(state)

    call_count = 0

    def fake_run_pipeline(config, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        output_root = kwargs.get("output_root", tmp_path / "outputs")
        return _make_success_result(config, Path(output_root) / f"sweep_run_{call_count}")

    monkeypatch.setattr(backtest_service_module, "run_pipeline", fake_run_pipeline)

    configs = []
    for i in range(3):
        c = RunConfig.model_validate(
            {
                "run_name": f"sweep_{i:03d}",
                "period": {"start_date": "2020-01-01", "end_date": "2020-01-31"},
                "data": {"parquet_root": str(tmp_path / "parquet")},
                "universe": {"mode": "custom_list", "tickers": ["AAPL"]},
                "output": {"dir": str(tmp_path / "outputs")},
            }
        )
        configs.append((f"sweep_{i:03d}", {"param": i}, c))

    async def scenario() -> None:
        job_id = service.start_sweep(configs, sweep_group_id="test_grp")
        for _ in range(400):
            job = service.get_job(job_id)
            if job is not None and job.status in {"ok", "error"}:
                break
            await asyncio.sleep(0.01)
        job = service.get_job(job_id)
        assert job is not None
        assert job.status == "ok"
        assert call_count == 3

        # Verify runs persisted with sweep group
        runs = service.list_runs()
        assert len(runs) == 3
        group_runs = state.db.list_runs_by_sweep_group("test_grp")
        assert len(group_runs) == 3

    asyncio.run(scenario())
