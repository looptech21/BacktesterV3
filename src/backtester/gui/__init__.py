"""GUI package exports."""

from __future__ import annotations

from backtester.gui.state import AppState, JobState, RunSummary, StateDB, init_app_state

__all__ = [
    "AppState",
    "JobState",
    "RunSummary",
    "StateDB",
    "init_app_state",
    "launch",
]


def launch(*args, **kwargs):
    """Lazy import wrapper to avoid importing NiceGUI at package import time."""
    from backtester.gui.app import launch as _launch

    return _launch(*args, **kwargs)
