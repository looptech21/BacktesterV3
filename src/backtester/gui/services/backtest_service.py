"""GUI service: orchestrate backtests and expose run history."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backtester.config import RunConfig
from backtester.engine.lean_runner import is_docker_available
from backtester.engine.runner import RunResult, run_pipeline
from backtester.gui.state import AppState, JobState, RunSummary
from backtester.io_utils import read_json


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class BacktestService:
    def __init__(self, state: AppState) -> None:
        self.state = state

    def probe_docker(self) -> tuple[bool, str]:
        if is_docker_available():
            return True, "Docker detected"
        return False, "Docker not detected (runs may be skipped in auto mode)"

    def start_run(self, config: RunConfig) -> str:
        if self.state.has_running_job():
            raise RuntimeError("A backtest job is already running")

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError("start_run must be called from a running event loop") from exc

        job_id = uuid4().hex[:12]
        job = JobState(job_id=job_id, run_name=config.run_name, status="queued", message="Queued")
        self.state.add_job(job)
        self.state.set_current_config(config)

        task = loop.create_task(self._execute_job(job_id, config))
        self.state.set_job_task(job_id, task)
        return job_id

    async def _execute_job(self, job_id: str, config: RunConfig) -> None:
        self.state.update_job(
            job_id,
            status="running",
            started_at=_utc_now_iso(),
            message="Starting pipeline",
            stage="convert",
            fraction=0.0,
        )
        self.state.append_job_event(job_id, stage="start", message="Pipeline started", fraction=0.0)

        def progress_callback(stage: str, message: str, fraction: float | None) -> None:
            self.state.update_job(job_id, stage=stage, message=message, fraction=fraction)
            self.state.append_job_event(job_id, stage=stage, message=message, fraction=fraction)

        try:
            result = await asyncio.to_thread(
                run_pipeline,
                config,
                output_root=self.state.output_root,
                progress_callback=progress_callback,
            )
            self._finalize_success(job_id, result)
        except Exception as exc:
            self.state.append_job_event(job_id, stage="error", message=str(exc), fraction=None)
            self.state.update_job(
                job_id,
                status="error",
                finished_at=_utc_now_iso(),
                error=str(exc),
                message="Pipeline failed",
            )
        finally:
            self.state.clear_active_job(job_id)

    def _finalize_success(self, job_id: str, result: RunResult, *, sweep_group_id: str | None = None) -> None:
        finished_at = _utc_now_iso()
        stage_dicts = [
            {
                "stage": stage.stage,
                "status": stage.status,
                "duration_ms": stage.duration_ms,
                "detail": stage.detail,
                "error": stage.error,
            }
            for stage in result.stages
        ]
        summary = RunSummary(
            id=None,
            run_name=result.run_name,
            experiment_id=result.experiment_id,
            status=result.status,
            run_dir=str(result.run_dir),
            created_at=result.manifest.get("timestamp_utc", finished_at),
            finished_at=finished_at,
            metrics=result.metrics,
            stages=stage_dicts,
            error=result.error,
            sweep_group_id=sweep_group_id,
        )
        run_id = self.state.db.save_run(summary)
        self.state.set_selected_run_id(run_id)
        self.state.append_job_event(job_id, stage="done", message=f"Run {result.status}", fraction=1.0)
        self.state.update_job(
            job_id,
            status=result.status,
            finished_at=finished_at,
            run_id=run_id,
            run_dir=str(result.run_dir),
            experiment_id=result.experiment_id,
            message=f"Pipeline finished ({result.status})",
            error=result.error,
        )

    def start_sweep(self, configs: list[tuple[str, dict[str, Any], RunConfig]], sweep_group_id: str) -> str:
        """Launch a sequential sweep of multiple configs as a single job."""
        if self.state.has_running_job():
            raise RuntimeError("A backtest job is already running")

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError("start_sweep must be called from a running event loop") from exc

        job_id = uuid4().hex[:12]
        first_name = configs[0][0] if configs else "sweep"
        run_label = f"{first_name} (+{len(configs) - 1} more)"
        job = JobState(job_id=job_id, run_name=run_label, status="queued", message="Queued")
        self.state.add_job(job)

        task = loop.create_task(self._execute_sweep(job_id, configs, sweep_group_id))
        self.state.set_job_task(job_id, task)
        return job_id

    async def _execute_sweep(
        self, job_id: str, configs: list[tuple[str, dict[str, Any], RunConfig]], sweep_group_id: str
    ) -> None:
        total = len(configs)
        ok_count = 0
        err_count = 0

        self.state.update_job(
            job_id,
            status="running",
            started_at=_utc_now_iso(),
            message=f"Starting sweep: {total} runs",
            stage="convert",
            fraction=0.0,
        )
        self.state.append_job_event(job_id, stage="start", message=f"Sweep started ({total} runs)", fraction=0.0)

        for i, (run_name, _combo, config) in enumerate(configs, start=1):
            fraction = (i - 1) / total
            self.state.update_job(job_id, message=f"Running {i}/{total} — {run_name}", fraction=fraction)
            self.state.append_job_event(
                job_id, stage="sweep", message=f"Starting {run_name} ({i}/{total})", fraction=fraction
            )

            idx = i  # bind loop variable for closure

            def progress_callback(stage: str, message: str, frac: float | None, _idx: int = idx) -> None:
                self.state.update_job(job_id, stage=stage, message=f"[{_idx}/{total}] {message}", fraction=frac)

            try:
                result = await asyncio.to_thread(
                    run_pipeline,
                    config,
                    output_root=self.state.output_root,
                    progress_callback=progress_callback,
                )
                self._save_sweep_run(result, sweep_group_id)
                if result.status == "ok":
                    ok_count += 1
                else:
                    err_count += 1
            except Exception as exc:
                err_count += 1
                self.state.append_job_event(
                    job_id, stage="error", message=f"{run_name} failed: {exc}", fraction=fraction
                )

        finished_at = _utc_now_iso()
        final_status = "ok" if ok_count > 0 else "error"
        self.state.append_job_event(
            job_id, stage="done", message=f"Sweep done: {ok_count} ok, {err_count} errors", fraction=1.0
        )
        self.state.update_job(
            job_id,
            status=final_status,
            finished_at=finished_at,
            message=f"Sweep finished: {ok_count}/{total} ok",
            fraction=1.0,
        )
        self.state.clear_active_job(job_id)

    def _save_sweep_run(self, result: RunResult, sweep_group_id: str) -> None:
        finished_at = _utc_now_iso()
        stage_dicts = [
            {
                "stage": stage.stage,
                "status": stage.status,
                "duration_ms": stage.duration_ms,
                "detail": stage.detail,
                "error": stage.error,
            }
            for stage in result.stages
        ]
        summary = RunSummary(
            id=None,
            run_name=result.run_name,
            experiment_id=result.experiment_id,
            status=result.status,
            run_dir=str(result.run_dir),
            created_at=result.manifest.get("timestamp_utc", finished_at),
            finished_at=finished_at,
            metrics=result.metrics,
            stages=stage_dicts,
            error=result.error,
            sweep_group_id=sweep_group_id,
        )
        run_id = self.state.db.save_run(summary)
        self.state.set_selected_run_id(run_id)

    def get_job(self, job_id: str) -> JobState | None:
        return self.state.get_job(job_id)

    def list_runs(self, limit: int = 200) -> list[RunSummary]:
        return self.state.db.list_runs(limit=limit)

    def get_run(self, run_id: int) -> RunSummary | None:
        return self.state.db.get_run(run_id)

    def load_run_artifacts(self, run_dir: Path) -> dict[str, Any]:
        run_dir = Path(run_dir)
        manifest = self._read_json_if_exists(run_dir / "run_manifest.json", default={})
        metrics = self._read_json_if_exists(run_dir / "metrics_summary.json", default={})
        trades = self._read_json_if_exists(run_dir / "trades.json", default=[])
        run_config = self._read_json_if_exists(run_dir / "run_config.json", default={})

        if not metrics and isinstance(manifest, dict):
            metrics = dict(manifest.get("metrics_summary", {}))

        stages = []
        if isinstance(manifest, dict):
            stages = list(manifest.get("stages", []))

        return {
            "manifest": manifest,
            "metrics": metrics,
            "trades": trades,
            "run_config": run_config,
            "stages": stages,
        }

    @staticmethod
    def _read_json_if_exists(path: Path, *, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return read_json(path)
        except Exception:
            return default
