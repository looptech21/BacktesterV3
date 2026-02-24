"""Results tab page."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import plotly.graph_objects as go
import yaml
from nicegui import events, ui

from backtester.gui.components.metrics_cards import MetricsCards
from backtester.gui.components.progress import PipelineProgress
from backtester.gui.components.trade_inspection import TradeInspectionPanel
from backtester.gui.services.backtest_service import BacktestService
from backtester.gui.state import AppState


def _query_param(name: str) -> str | None:
    try:
        value = ui.context.client.request.query_params.get(name)
    except Exception:
        value = None
    return value


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _fmt_number(value: Any, precision: int = 2) -> str:
    number = _safe_float(value, 0.0)
    return f"{number:,.{precision}f}"


def _fmt_percent(value: Any, precision: int = 2) -> str:
    number = _safe_float(value, 0.0)
    return f"{number * 100:.{precision}f}%"


def trade_row_id(trade: dict[str, Any], index: int) -> str:
    raw = trade.get("trade_id")
    if raw in (None, ""):
        return f"trade_{index}"
    return str(raw)


def pick_selected_trade_id(trades: list[dict[str, Any]], preferred_trade_id: str | None) -> str | None:
    if not trades:
        return None
    available = {trade_row_id(trade, idx) for idx, trade in enumerate(trades)}
    if preferred_trade_id and preferred_trade_id in available:
        return preferred_trade_id
    return trade_row_id(trades[0], 0)


def get_trade_by_row_id(trades: list[dict[str, Any]], row_id: str | None) -> dict[str, Any] | None:
    if not row_id:
        return None
    for idx, trade in enumerate(trades):
        if trade_row_id(trade, idx) == row_id:
            return trade
    return None


def _prepare_trade_rows(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, trade in enumerate(trades):
        row = dict(trade)
        row_id = trade_row_id(trade, idx)
        row["_trade_row_id"] = row_id
        if row.get("trade_id") in (None, ""):
            row["trade_id"] = row_id
        row["entry_price"] = _fmt_number(trade.get("entry_price"))
        row["exit_price"] = _fmt_number(trade.get("exit_price"))
        row["pnl_net"] = _fmt_number(trade.get("pnl_net"))
        row["return_pct"] = _fmt_percent(trade.get("return_pct"))
        rows.append(row)
    return rows


def render_results_page(state: AppState, backtest_service: BacktestService) -> None:
    job_id_hint = _query_param("job_id")
    run_id_hint = _query_param("run_id")
    page_state: dict[str, Any] = {"selected_trade_id": None}

    with ui.column().classes("w-full max-w-[1200px] mx-auto gap-4 p-4"):
        ui.label("Results").classes("text-3xl font-bold")
        ui.label("Inspect metrics, equity curve, fills, and pipeline stage outcomes.").classes("text-slate-500")

        run_select = ui.select(label="Run", options={}).classes("min-w-[420px]")
        metrics_cards = MetricsCards()
        progress = PipelineProgress()
        chart_container = ui.column().classes("w-full")
        trades_container = ui.column().classes("w-full")
        inspection_panel = TradeInspectionPanel()
        details_container = ui.column().classes("w-full")

        def refresh_run_options() -> dict[str, str]:
            runs = backtest_service.list_runs(limit=300)
            options = {
                str(item.id): f"{item.run_name} ({item.status})"
                for item in runs
                if item.id is not None
            }
            run_select.options = options
            run_select.update()
            return options

        def render_artifacts(run_id: int) -> None:
            summary = backtest_service.get_run(run_id)
            if summary is None:
                ui.notify(f"Run not found: {run_id}", type="warning")
                return

            state.set_selected_run_id(run_id)
            artifacts = backtest_service.load_run_artifacts(Path(summary.run_dir))
            metrics = artifacts.get("metrics", {}) or summary.metrics
            trades = artifacts.get("trades", [])
            if not isinstance(trades, list):
                trades = []
            stages = artifacts.get("stages", []) or summary.stages
            run_config = artifacts.get("run_config", {})
            if not isinstance(run_config, dict):
                run_config = {}
            manifest = artifacts.get("manifest", {})
            if "starting_balance" not in metrics:
                initial_cash = (
                    (run_config.get("execution") or {}).get("initial_cash")
                    if isinstance(run_config, dict)
                    else None
                )
                metrics["starting_balance"] = _safe_float(initial_cash, 0.0)
            if "max_drawdown_pct" not in metrics:
                starting_balance = _safe_float(metrics.get("starting_balance"), 0.0)
                drawdown = _safe_float(metrics.get("max_drawdown"), 0.0)
                metrics["max_drawdown_pct"] = (drawdown / starting_balance) if starting_balance > 0 else 0.0

            metrics_cards.set_metrics(metrics)
            progress.set_from_stages(stages)

            chart_container.clear()
            curve = metrics.get("equity_curve", [])
            with chart_container, ui.card().classes("w-full"):
                ui.label("Equity Curve (cumulative PnL)").classes("text-lg font-semibold")
                if curve:
                    y_values = [_safe_float(v) for v in curve]
                    fig = go.Figure()
                    fig.add_trace(
                        go.Scatter(
                            x=list(range(1, len(y_values) + 1)),
                            y=y_values,
                            mode="lines",
                            name="PnL",
                        )
                    )
                    fig.update_layout(height=320, margin=dict(l=20, r=20, t=30, b=20))
                    ui.plotly(fig).classes("w-full")
                else:
                    ui.label("No equity curve data found for this run.")

            trades_container.clear()
            inspection_panel.set_context(run_config, trades)
            with trades_container, ui.card().classes("w-full"):
                ui.label(f"Trades ({len(trades)})").classes("text-lg font-semibold")
                if trades:
                    rows = _prepare_trade_rows(trades)
                    rows_by_id = {str(row["_trade_row_id"]): row for row in rows}

                    def on_trade_select(e: events.TableSelectionEventArguments) -> None:
                        selected_row = e.selection[0] if e.selection else None
                        selected_id = str((selected_row or {}).get("_trade_row_id") or "").strip() or None
                        page_state["selected_trade_id"] = selected_id
                        inspection_panel.set_active_trade(get_trade_by_row_id(trades, selected_id))

                    table = ui.table(
                        columns=[
                            {"name": "trade_id", "label": "Trade ID", "field": "trade_id"},
                            {"name": "symbol", "label": "Symbol", "field": "symbol"},
                            {"name": "setup_type", "label": "Setup", "field": "setup_type"},
                            {"name": "entry_fill_ts_utc", "label": "Entry TS", "field": "entry_fill_ts_utc"},
                            {"name": "entry_price", "label": "Entry", "field": "entry_price"},
                            {"name": "exit_fill_ts_utc", "label": "Exit TS", "field": "exit_fill_ts_utc"},
                            {"name": "exit_price", "label": "Exit", "field": "exit_price"},
                            {"name": "pnl_net", "label": "PnL Net", "field": "pnl_net"},
                            {"name": "return_pct", "label": "Return", "field": "return_pct"},
                            {"name": "exit_reason", "label": "Exit Reason", "field": "exit_reason"},
                        ],
                        rows=rows,
                        row_key="_trade_row_id",
                        pagination=20,
                        selection="single",
                        on_select=on_trade_select,
                    ).classes("w-full")

                    selected_id = pick_selected_trade_id(trades, page_state.get("selected_trade_id"))
                    page_state["selected_trade_id"] = selected_id
                    if selected_id is not None:
                        selected_trade = get_trade_by_row_id(trades, selected_id)
                        inspection_panel.set_active_trade(selected_trade)
                        selected_row = rows_by_id.get(selected_id)
                        if selected_row is not None:
                            table.selected = [selected_row]
                            table.update()
                else:
                    inspection_panel.set_active_trade(None)
                    ui.label("No trades found.")

            details_container.clear()
            with details_container:
                with ui.expansion("Run Config", icon="description").classes("w-full"):
                    ui.code(yaml.safe_dump(run_config or {}, sort_keys=False), language="yaml").classes("w-full")
                with ui.expansion("Run Manifest", icon="receipt_long").classes("w-full"):
                    ui.code(yaml.safe_dump(manifest or {}, sort_keys=False), language="yaml").classes("w-full")

        def on_run_select() -> None:
            if not run_select.value:
                return
            render_artifacts(int(run_select.value))

        run_select.on("update:model-value", lambda _: on_run_select())

        options = refresh_run_options()
        selected_id: int | None = None
        if run_id_hint and run_id_hint.isdigit():
            selected_id = int(run_id_hint)
        elif state.get_selected_run_id() is not None:
            selected_id = state.get_selected_run_id()
        elif options:
            selected_id = int(next(iter(options.keys())))

        if selected_id is not None and str(selected_id) in options:
            run_select.value = str(selected_id)
            render_artifacts(selected_id)

        live_job = {"job_id": job_id_hint, "announced": False}

        def poll_live_job() -> None:
            if not live_job["job_id"]:
                return
            job = backtest_service.get_job(str(live_job["job_id"]))
            if job is None:
                return
            progress.set_from_job(job)
            if job.status in {"ok", "error", "skipped"}:
                if not live_job["announced"]:
                    notify_type = "positive" if job.status == "ok" else "warning"
                    ui.notify(f"Run finished with status: {job.status}", type=notify_type)
                    live_job["announced"] = True
                live_job["job_id"] = None
                refresh_run_options()
                if job.run_id is not None:
                    run_select.value = str(job.run_id)
                    render_artifacts(job.run_id)
                elif job.error:
                    ui.notify(job.error, type="negative")

        ui.timer(1.0, poll_live_job)
