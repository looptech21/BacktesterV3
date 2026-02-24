"""GUI state management.

This module combines:
- SQLite persistence for saved configs and run history.
- In-memory session state for currently edited config and active jobs.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from backtester.config import RunConfig

JobStatus = Literal["queued", "running", "ok", "error", "skipped"]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunSummary:
    id: int | None
    run_name: str
    experiment_id: str
    status: str
    run_dir: str
    created_at: str
    finished_at: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    stages: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    sweep_group_id: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RunSummary:
        col_names = row.keys()
        return cls(
            id=int(row["id"]),
            run_name=str(row["run_name"]),
            experiment_id=str(row["experiment_id"]),
            status=str(row["status"]),
            run_dir=str(row["run_dir"]),
            created_at=str(row["created_at"]),
            finished_at=row["finished_at"],
            metrics=json.loads(row["metrics_json"] or "{}"),
            stages=json.loads(row["stages_json"] or "[]"),
            error=row["error"],
            sweep_group_id=row["sweep_group_id"] if "sweep_group_id" in col_names else None,
        )


@dataclass
class JobState:
    job_id: str
    run_name: str
    status: JobStatus = "queued"
    created_at: str = field(default_factory=_utc_now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    stage: str | None = None
    message: str | None = None
    fraction: float | None = None
    error: str | None = None
    run_id: int | None = None
    run_dir: str | None = None
    experiment_id: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    task: asyncio.Task[Any] | None = field(default=None, repr=False)


class StateDB:
    """SQLite-backed persistence for GUI state."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        schema = """
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS saved_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            experiment_id TEXT NOT NULL,
            yaml_text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_name TEXT NOT NULL,
            experiment_id TEXT NOT NULL,
            status TEXT NOT NULL,
            run_dir TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            finished_at TEXT,
            metrics_json TEXT NOT NULL,
            stages_json TEXT NOT NULL,
            error TEXT,
            sweep_group_id TEXT
        );

        CREATE TABLE IF NOT EXISTS ui_prefs (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
        with self._connect() as conn:
            conn.executescript(schema)
            # Migration for existing DBs that lack the sweep_group_id column.
            try:
                conn.execute("ALTER TABLE runs ADD COLUMN sweep_group_id TEXT")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

    # ------------------------------------------------------------------
    # Saved config CRUD
    # ------------------------------------------------------------------

    def save_config(self, name: str, experiment_id: str, yaml_text: str) -> int:
        now = _utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO saved_configs (name, experiment_id, yaml_text, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    experiment_id=excluded.experiment_id,
                    yaml_text=excluded.yaml_text,
                    updated_at=excluded.updated_at
                """,
                (name, experiment_id, yaml_text, now, now),
            )
            row = conn.execute("SELECT id FROM saved_configs WHERE name = ?", (name,)).fetchone()
            conn.commit()
        if row is None:
            raise RuntimeError("failed to persist config")
        return int(row["id"])

    def list_configs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, experiment_id, created_at, updated_at
                FROM saved_configs
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "experiment_id": str(row["experiment_id"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def get_config_yaml(self, config_id: int) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT yaml_text FROM saved_configs WHERE id = ?",
                (int(config_id),),
            ).fetchone()
        if row is None:
            return None
        return str(row["yaml_text"])

    # ------------------------------------------------------------------
    # Run history CRUD
    # ------------------------------------------------------------------

    def save_run(self, summary: RunSummary) -> int:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_name, experiment_id, status, run_dir, created_at, finished_at,
                    metrics_json, stages_json, error, sweep_group_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_dir) DO UPDATE SET
                    run_name=excluded.run_name,
                    experiment_id=excluded.experiment_id,
                    status=excluded.status,
                    finished_at=excluded.finished_at,
                    metrics_json=excluded.metrics_json,
                    stages_json=excluded.stages_json,
                    error=excluded.error,
                    sweep_group_id=excluded.sweep_group_id
                """,
                (
                    summary.run_name,
                    summary.experiment_id,
                    summary.status,
                    summary.run_dir,
                    summary.created_at,
                    summary.finished_at,
                    json.dumps(summary.metrics, default=str),
                    json.dumps(summary.stages, default=str),
                    summary.error,
                    summary.sweep_group_id,
                ),
            )
            row = conn.execute("SELECT id FROM runs WHERE run_dir = ?", (summary.run_dir,)).fetchone()
            conn.commit()
        if row is None:
            raise RuntimeError("failed to persist run summary")
        return int(row["id"])

    def list_runs(self, limit: int = 200) -> list[RunSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM runs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [RunSummary.from_row(row) for row in rows]

    def get_run(self, run_id: int) -> RunSummary | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (int(run_id),)).fetchone()
        if row is None:
            return None
        return RunSummary.from_row(row)

    def list_sweep_groups(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT sweep_group_id, COUNT(*) as run_count, MIN(created_at) as created_at
                FROM runs
                WHERE sweep_group_id IS NOT NULL
                GROUP BY sweep_group_id
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [
            {
                "group_id": str(row["sweep_group_id"]),
                "run_count": int(row["run_count"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def list_runs_by_sweep_group(self, group_id: str) -> list[RunSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE sweep_group_id = ? ORDER BY created_at ASC",
                (group_id,),
            ).fetchall()
        return [RunSummary.from_row(row) for row in rows]

    # ------------------------------------------------------------------
    # UI preferences
    # ------------------------------------------------------------------

    def set_ui_pref(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ui_prefs (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )
            conn.commit()

    def get_ui_pref(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM ui_prefs WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return str(row["value"])


class AppState:
    """Thread-safe in-memory state for the running GUI process."""

    def __init__(self, output_root: Path, db: StateDB) -> None:
        self.output_root = output_root
        self.db = db
        self._lock = threading.RLock()
        self.jobs: dict[str, JobState] = {}
        self.active_job_id: str | None = None
        self.current_config: RunConfig | None = None

        selected = self.db.get_ui_pref("selected_run_id")
        self.selected_run_id: int | None = int(selected) if selected and selected.isdigit() else None

    # ------------------------------------------------------------------
    # Config session state
    # ------------------------------------------------------------------

    def set_current_config(self, config: RunConfig) -> None:
        with self._lock:
            self.current_config = config

    def get_current_config(self) -> RunConfig | None:
        with self._lock:
            return self.current_config

    # ------------------------------------------------------------------
    # Run selection state
    # ------------------------------------------------------------------

    def set_selected_run_id(self, run_id: int | None) -> None:
        with self._lock:
            self.selected_run_id = run_id
        if run_id is None:
            self.db.set_ui_pref("selected_run_id", "")
        else:
            self.db.set_ui_pref("selected_run_id", str(run_id))

    def get_selected_run_id(self) -> int | None:
        with self._lock:
            return self.selected_run_id

    # ------------------------------------------------------------------
    # Job lifecycle
    # ------------------------------------------------------------------

    def has_running_job(self) -> bool:
        with self._lock:
            if self.active_job_id is None:
                return False
            active = self.jobs.get(self.active_job_id)
            if active is None:
                return False
            return active.status in {"queued", "running"}

    def add_job(self, job: JobState) -> None:
        with self._lock:
            if self.has_running_job():
                raise RuntimeError("another backtest job is already running")
            self.jobs[job.job_id] = job
            self.active_job_id = job.job_id

    def set_job_task(self, job_id: str, task: asyncio.Task[Any]) -> None:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return
            job.task = task

    def update_job(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return
            for key, value in changes.items():
                if hasattr(job, key):
                    setattr(job, key, value)

    def append_job_event(self, job_id: str, *, stage: str, message: str, fraction: float | None = None) -> None:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return
            job.events.append(
                {
                    "ts": _utc_now_iso(),
                    "stage": stage,
                    "message": message,
                    "fraction": fraction,
                }
            )
            if len(job.events) > 500:
                job.events = job.events[-500:]

    def get_job(self, job_id: str) -> JobState | None:
        with self._lock:
            return self.jobs.get(job_id)

    def list_jobs(self) -> list[JobState]:
        with self._lock:
            return sorted(self.jobs.values(), key=lambda job: job.created_at, reverse=True)

    def clear_active_job(self, job_id: str) -> None:
        with self._lock:
            if self.active_job_id == job_id:
                self.active_job_id = None


def init_app_state(output_root: Path) -> AppState:
    """Create and initialize app state for a given output root."""
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    db = StateDB(output_root / "gui_state.db")
    return AppState(output_root=output_root, db=db)
