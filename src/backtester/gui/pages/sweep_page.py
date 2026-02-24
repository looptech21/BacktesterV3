"""Sweep tab page: parameter variation designer and sweep execution."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from nicegui import ui

from backtester.config import SweepConfig, SweepGridConfig
from backtester.gui.services.backtest_service import BacktestService
from backtester.gui.state import AppState
from backtester.sweep.grid import expand_sweep, grid_combinations

# Sweepable parameters grouped by domain.
SWEEPABLE_PARAMS: dict[str, list[str]] = {
    "Common Breakout": [
        "setups.common_breakout.orh_window_minutes",
        "setups.common_breakout.partial_exit_day",
        "setups.common_breakout.partial_exit_fraction",
        "setups.common_breakout.trailing_ma_days",
        "setups.common_breakout.max_stop_multiple",
        "setups.common_breakout.impulse_lookback_days",
        "setups.common_breakout.min_impulse_return_pct",
    ],
    "Episodic Pivot": [
        "setups.episodic_pivot.min_gap_pct",
        "setups.episodic_pivot.orh_window_minutes",
        "setups.episodic_pivot.trailing_ma_days",
        "setups.episodic_pivot.max_stop_multiple",
        "setups.episodic_pivot.trailing_switch_mode",
    ],
    "Execution": [
        "execution.risk_per_trade",
        "execution.slippage_bps",
        "execution.max_positions",
    ],
}

MAX_COMBOS = 200
WARN_COMBOS = 50


def _get_nested(data: dict[str, Any], dotted_key: str) -> Any:
    """Read a value from a nested dict using a dotted path."""
    parts = dotted_key.split(".")
    target = data
    for part in parts:
        if not isinstance(target, dict):
            return None
        target = target.get(part)
        if target is None:
            return None
    return target


def _short_name(dotted: str) -> str:
    """Return last part of dotted path for display."""
    return dotted.rsplit(".", 1)[-1]


def _parse_values(text: str) -> list[Any]:
    """Parse comma-separated values, auto-detecting int/float/string."""
    result: list[Any] = []
    for raw in text.split(","):
        v = raw.strip()
        if not v:
            continue
        try:
            if "." in v:
                result.append(float(v))
            else:
                result.append(int(v))
        except ValueError:
            result.append(v)
    return result


def render_sweep_page(state: AppState, backtest_service: BacktestService) -> None:
    config = state.get_current_config()
    config_dict = config.model_dump(mode="json") if config else {}

    # Reactive state for sweep dimensions: {dotted_path: "comma,values"}
    dimensions: dict[str, str] = {}

    with ui.column().classes("w-full max-w-[1200px] mx-auto gap-4 p-4"):
        ui.label("Sweep").classes("text-3xl font-bold")

        # -- Base config summary --
        with ui.card().classes("w-full"):
            ui.label("Base Config Summary").classes("text-lg font-semibold")
            if config is None:
                ui.label("No active config yet. Build a baseline in Configure first.").classes("text-orange-600")
            else:
                with ui.grid(columns=2).classes("w-full gap-2"):
                    ui.label(f"Run Name: {config.run_name}")
                    ui.label(f"Period: {config.period.start_date} to {config.period.end_date}")
                    ui.label(f"Universe Mode: {config.universe.mode}")
                    ui.label(f"Tickers: {', '.join(config.universe.tickers[:8]) or '-'}")
                    ui.label(f"Common Breakout: {'ON' if config.setups.common_breakout.enabled else 'OFF'}")
                    ui.label(f"Episodic Pivot: {'ON' if config.setups.episodic_pivot.enabled else 'OFF'}")

        # -- Sweep dimensions card --
        with ui.card().classes("w-full"):
            ui.label("Sweep Dimensions").classes("text-lg font-semibold")

            combo_label = ui.label("0 scenarios").classes("text-sm text-slate-500")
            combo_warning = ui.label("").classes("text-sm text-orange-600")
            dims_container = ui.column().classes("w-full gap-2")

            # Build flat list for the dropdown
            all_params: list[str] = []
            for params in SWEEPABLE_PARAMS.values():
                all_params.extend(params)
            param_options = {p: f"{_short_name(p)}  ({p})" for p in all_params}

            param_select = ui.select(
                label="Add parameter dimension",
                options=param_options,
            ).classes("min-w-[400px]")

            def _update_combo_count() -> None:
                grid_vals: dict[str, list[Any]] = {}
                for path, text in dimensions.items():
                    vals = _parse_values(text)
                    if vals:
                        grid_vals[path] = vals
                if not grid_vals:
                    combo_label.text = "0 scenarios"
                    combo_warning.text = ""
                    return
                combos = grid_combinations(grid_vals)
                n = len(combos)
                parts = " x ".join(str(len(v)) for v in grid_vals.values())
                combo_label.text = f"{parts} = {n} scenarios"
                if n > MAX_COMBOS:
                    combo_warning.text = f"Exceeds maximum of {MAX_COMBOS} combinations!"
                elif n > WARN_COMBOS:
                    combo_warning.text = f"Warning: {n} scenarios may take a long time."
                else:
                    combo_warning.text = ""

            def _add_dimension_row(dotted_path: str) -> None:
                default_val = _get_nested(config_dict, dotted_path)
                default_str = str(default_val) if default_val is not None else ""
                dimensions[dotted_path] = default_str

                with dims_container:
                    row = ui.row().classes("w-full items-center gap-2")
                    with row:
                        ui.label(_short_name(dotted_path)).classes("w-[200px] font-mono text-sm")
                        ui.label(f"Default: {default_str}").classes("w-[120px] text-xs text-slate-500")
                        inp = ui.input(
                            label="Values (comma-separated)",
                            value=default_str,
                        ).classes("flex-grow")

                        def _on_change(e: Any, path: str = dotted_path) -> None:
                            dimensions[path] = e.value
                            _update_combo_count()

                        inp.on("change", _on_change)

                        def _remove(path: str = dotted_path, r: Any = row) -> None:
                            dimensions.pop(path, None)
                            r.delete()
                            _update_combo_count()

                        ui.button(icon="delete", on_click=_remove).props("flat dense color=negative")

                _update_combo_count()

            def _on_add_dimension() -> None:
                chosen = param_select.value
                if not chosen:
                    ui.notify("Select a parameter first", type="warning")
                    return
                if chosen in dimensions:
                    ui.notify("Dimension already added", type="info")
                    return
                _add_dimension_row(chosen)
                param_select.value = None

            ui.button("Add Dimension", on_click=_on_add_dimension, icon="add").props("color=primary")

        # -- Run prefix and button --
        with ui.card().classes("w-full"):
            prefix_input = ui.input(label="Run Prefix", value="sweep").classes("w-[300px]")

            async def _run_sweep() -> None:
                if config is None:
                    ui.notify("No active config. Configure a baseline first.", type="negative")
                    return
                grid_vals: dict[str, list[Any]] = {}
                for path, text in dimensions.items():
                    vals = _parse_values(text)
                    if vals:
                        grid_vals[path] = vals
                if not grid_vals:
                    ui.notify("Add at least one sweep dimension.", type="warning")
                    return
                combos = grid_combinations(grid_vals)
                if len(combos) > MAX_COMBOS:
                    ui.notify(f"Too many combinations ({len(combos)} > {MAX_COMBOS}).", type="negative")
                    return

                sweep_cfg = SweepConfig(
                    base_config="inline",
                    run_prefix=prefix_input.value or "sweep",
                    grid=SweepGridConfig(values=grid_vals),
                )
                expanded = expand_sweep(sweep_cfg, config)
                sweep_group_id = uuid4().hex[:12]

                try:
                    backtest_service.start_sweep(expanded, sweep_group_id)
                    ui.notify(f"Sweep started: {len(expanded)} runs", type="positive")
                except RuntimeError as exc:
                    ui.notify(str(exc), type="negative")

            ui.button("Run Sweep", on_click=_run_sweep, icon="play_arrow").props("color=primary")

        # -- Sweep history --
        with ui.card().classes("w-full"):
            ui.label("Sweep History").classes("text-lg font-semibold")
            groups = state.db.list_sweep_groups()
            if not groups:
                ui.label("No sweeps run yet.").classes("text-slate-500")
            else:
                rows = [
                    {
                        "group_id": g["group_id"],
                        "runs": g["run_count"],
                        "created": str(g["created_at"])[:19].replace("T", " "),
                    }
                    for g in groups
                ]
                ui.table(
                    columns=[
                        {"name": "group_id", "label": "Sweep Group", "field": "group_id"},
                        {"name": "runs", "label": "Runs", "field": "runs"},
                        {"name": "created", "label": "Created", "field": "created"},
                    ],
                    rows=rows,
                    row_key="group_id",
                    pagination=10,
                ).classes("w-full")
