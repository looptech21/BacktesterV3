"""Pipeline progress component for the 5-stage engine flow."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from backtester.gui.state import JobState

STAGE_ORDER = ["convert", "lean", "parse", "match", "metrics"]
STAGE_LABELS = {
    "convert": "Convert Data",
    "lean": "Run LEAN",
    "parse": "Parse Results",
    "match": "Match Trades",
    "metrics": "Compute Metrics",
}


def _status_color(status: str) -> str:
    if status in {"ok", "done"}:
        return "positive"
    if status in {"running", "queued"}:
        return "primary"
    if status == "skipped":
        return "warning"
    if status == "error":
        return "negative"
    return "grey"


class PipelineProgress:
    def __init__(self) -> None:
        self.stage_badges: dict[str, Any] = {}
        self.stage_meta: dict[str, Any] = {}
        self.events_log: Any | None = None
        self.summary_label: Any | None = None

        self.root = ui.column().classes("w-full gap-2")
        with self.root:
            self.summary_label = ui.label("Pipeline idle").classes("text-sm text-slate-500")
            with ui.row().classes("w-full gap-2 items-stretch"):
                for stage in STAGE_ORDER:
                    with ui.card().classes("min-w-[150px] flex-1"):
                        ui.label(STAGE_LABELS[stage]).classes("text-xs uppercase tracking-wider text-slate-500")
                        badge = ui.badge("pending", color="grey")
                        meta = ui.label("").classes("text-xs text-slate-500")
                        self.stage_badges[stage] = badge
                        self.stage_meta[stage] = meta
            self.events_log = ui.textarea(
                label="Events",
                value="",
            ).props("readonly autogrow").classes("w-full text-xs")

    def set_from_job(self, job: JobState | None) -> None:
        if job is None:
            self._set_summary("No active job")
            self._set_stage_state({})
            self._write_log([])
            return

        states: dict[str, dict[str, str]] = {stage: {"status": "pending", "meta": ""} for stage in STAGE_ORDER}
        for event in job.events:
            stage = str(event.get("stage", ""))
            if stage not in states:
                continue
            states[stage]["status"] = "running"
            states[stage]["meta"] = str(event.get("message", ""))

        if job.stage in states:
            states[job.stage]["status"] = job.status if job.status in {"ok", "error", "skipped"} else "running"
            if job.message:
                states[job.stage]["meta"] = job.message

        if job.status in {"ok", "error", "skipped"}:
            for stage in STAGE_ORDER:
                if states[stage]["status"] == "running":
                    states[stage]["status"] = "ok"
            if job.stage in states:
                states[job.stage]["status"] = job.status

        self._set_summary(f"{job.run_name}: {job.status}" + (f" - {job.message}" if job.message else ""))
        self._set_stage_state(states)

        log_lines = [
            f"{event.get('ts', '')} | {event.get('stage', '')} | {event.get('message', '')}"
            for event in job.events[-40:]
        ]
        self._write_log(log_lines)

    def set_from_stages(self, stages: list[dict[str, Any]]) -> None:
        states: dict[str, dict[str, str]] = {stage: {"status": "pending", "meta": ""} for stage in STAGE_ORDER}
        for stage in stages:
            name = str(stage.get("stage", ""))
            if name not in states:
                continue
            status = str(stage.get("status", "pending"))
            meta = f"{stage.get('duration_ms', 0)} ms"
            if stage.get("error"):
                meta = f"{meta} | {stage.get('error')}"
            states[name] = {"status": status, "meta": meta}
        self._set_summary("Stage timeline from run artifacts")
        self._set_stage_state(states)
        self._write_log([])

    def _set_summary(self, text: str) -> None:
        if self.summary_label is not None:
            self.summary_label.text = text

    def _set_stage_state(self, states: dict[str, dict[str, str]]) -> None:
        for stage in STAGE_ORDER:
            status = states.get(stage, {}).get("status", "pending")
            meta = states.get(stage, {}).get("meta", "")
            badge = self.stage_badges[stage]
            badge.text = status
            badge.color = _status_color(status)
            self.stage_meta[stage].text = meta

    def _write_log(self, lines: list[str]) -> None:
        if self.events_log is None:
            return
        self.events_log.value = "\n".join(lines)
