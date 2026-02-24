"""Compare tab page: multi-run comparison with trader score and equity overlay."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nicegui import ui

from backtester.analysis.comparison import compute_trader_score
from backtester.gui.services.backtest_service import BacktestService
from backtester.gui.state import RunSummary


def _as_float(metrics: dict[str, Any], key: str) -> float:
    try:
        return float(metrics.get(key, 0.0) or 0.0)
    except Exception:
        return 0.0


def _build_rows(runs: list[RunSummary]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run in runs:
        metrics = run.metrics or {}
        rows.append(
            {
                "id": run.id,
                "run_name": run.run_name,
                "status": run.status,
                "trades": int(metrics.get("num_trades", 0) or 0),
                "net_pnl": round(_as_float(metrics, "net_pnl"), 2),
                "win_rate": round(_as_float(metrics, "win_rate") * 100, 2),
                "profit_factor": round(_as_float(metrics, "profit_factor"), 2),
                "max_drawdown": round(_as_float(metrics, "max_drawdown"), 2),
                "sharpe": round(_as_float(metrics, "sharpe"), 2),
                "score": compute_trader_score(metrics),
                "created_at": run.created_at[:19].replace("T", " "),
            }
        )
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def _build_equity_chart(runs: list[RunSummary], backtest_service: BacktestService) -> Any:
    """Build a Plotly figure with overlaid equity curves for selected runs."""
    import plotly.graph_objects as go

    fig = go.Figure()
    for run in runs:
        if not run.run_dir:
            continue
        artifacts = backtest_service.load_run_artifacts(Path(run.run_dir))
        equity = artifacts.get("metrics", {}).get("equity_curve", [])
        if not equity:
            continue
        fig.add_trace(
            go.Scatter(
                y=equity,
                mode="lines",
                name=run.run_name,
            )
        )
    fig.update_layout(
        title="Equity Curve Overlay",
        xaxis_title="Trade #",
        yaxis_title="Cumulative PnL",
        height=400,
        margin={"l": 50, "r": 20, "t": 40, "b": 40},
    )
    return fig


def render_compare_page(backtest_service: BacktestService) -> None:
    with ui.column().classes("w-full max-w-[1200px] mx-auto gap-4 p-4"):
        ui.label("Compare").classes("text-3xl font-bold")

        # -- Filter bar --
        with ui.row().classes("w-full items-center gap-2"):
            groups = backtest_service.state.db.list_sweep_groups()
            group_options: dict[str, str] = {"all": "All Runs"}
            for g in groups:
                gid = g["group_id"]
                group_options[gid] = f"Sweep {gid} ({g['run_count']} runs)"

            group_select = ui.select(
                label="Filter",
                options=group_options,
                value="all",
            ).classes("min-w-[300px]")

            # Containers that will be rebuilt on filter change
            table_container = ui.column().classes("w-full")
            chart_container = ui.column().classes("w-full")
            nav_container = ui.column().classes("w-full")

        def _refresh() -> None:
            table_container.clear()
            chart_container.clear()
            nav_container.clear()

            selected_group = group_select.value
            if selected_group and selected_group != "all":
                runs = backtest_service.state.db.list_runs_by_sweep_group(selected_group)
            else:
                runs = backtest_service.list_runs(limit=300)

            rows = _build_rows(runs)

            # -- Comparison table --
            with table_container, ui.card().classes("w-full"):
                ui.label(f"Runs: {len(rows)}").classes("text-lg font-semibold")
                ui.table(
                    columns=[
                        {"name": "run_name", "label": "Run", "field": "run_name", "sortable": True},
                        {"name": "status", "label": "Status", "field": "status", "sortable": True},
                        {"name": "trades", "label": "Trades", "field": "trades", "sortable": True},
                        {"name": "net_pnl", "label": "Net PnL", "field": "net_pnl", "sortable": True},
                        {"name": "win_rate", "label": "Win Rate %", "field": "win_rate", "sortable": True},
                        {"name": "profit_factor", "label": "PF", "field": "profit_factor", "sortable": True},
                        {"name": "max_drawdown", "label": "Max DD", "field": "max_drawdown", "sortable": True},
                        {"name": "sharpe", "label": "Sharpe", "field": "sharpe", "sortable": True},
                        {"name": "score", "label": "Score", "field": "score", "sortable": True},
                        {"name": "created_at", "label": "Created", "field": "created_at", "sortable": True},
                    ],
                    rows=rows,
                    row_key="id",
                    pagination=20,
                ).classes("w-full")

            # -- Equity curve overlay (top 5 by score) --
            top_runs = [r for r in runs if r.status == "ok"][:5]
            if top_runs:
                with chart_container, ui.card().classes("w-full"):
                    ui.label("Equity Curve Overlay (Top 5)").classes("text-lg font-semibold")
                    fig = _build_equity_chart(top_runs, backtest_service)
                    ui.plotly(fig).classes("w-full")

            # -- Navigate to results --
            with nav_container, ui.card().classes("w-full"):
                ui.label("Open Run in Results").classes("text-lg font-semibold")
                run_options = {str(r.id): f"{r.run_name} ({r.status})" for r in runs if r.id is not None}
                run_select = ui.select(label="Choose Run", options=run_options).classes("min-w-[360px]")

                def on_open() -> None:
                    if not run_select.value:
                        ui.notify("Select a run first", type="warning")
                        return
                    ui.navigate.to(f"/results?run_id={int(run_select.value)}")

                ui.button("Open Results", on_click=on_open).props("color=primary")

        # Initial render
        _refresh()

        # Re-render on filter change
        group_select.on("update:model-value", lambda _: _refresh())

        # Refresh button
        ui.button("Refresh", on_click=_refresh, icon="refresh").props("flat color=primary")
