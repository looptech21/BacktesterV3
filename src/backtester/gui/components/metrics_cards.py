"""Metrics card grid component."""

from __future__ import annotations

from typing import Any

from nicegui import ui


def _fmt(value: Any, *, pct: bool = False, precision: int = 2) -> str:
    try:
        number = float(value)
    except Exception:
        return "-"
    if pct:
        return f"{number * 100:.{precision}f}%"
    return f"{number:,.{precision}f}"


class MetricsCards:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.root = ui.grid(columns=3).classes("w-full gap-3")
        self._render({})

    def set_metrics(self, metrics: dict[str, Any]) -> None:
        self._render(metrics)

    def _render(self, metrics: dict[str, Any]) -> None:
        self.root.clear()
        start_balance = metrics.get("starting_balance")
        if start_balance in (None, ""):
            start_balance = 0.0
        cards = [
            ("Starting Balance", _fmt(start_balance, precision=2)),
            ("Net PnL", _fmt(metrics.get("net_pnl", 0.0), precision=2)),
            ("Win Rate", _fmt(metrics.get("win_rate", 0.0), pct=True, precision=2)),
            ("Profit Factor", _fmt(metrics.get("profit_factor", 0.0), precision=2)),
            ("Max Drawdown", _fmt(metrics.get("max_drawdown", 0.0), precision=2)),
            ("Max Drawdown %", _fmt(metrics.get("max_drawdown_pct", 0.0), pct=True, precision=2)),
            ("Sharpe", _fmt(metrics.get("sharpe", 0.0), precision=2)),
            ("Trades", f"{int(metrics.get('num_trades', 0) or 0)}"),
        ]
        with self.root:
            for title, value in cards:
                with ui.card().classes("w-full"):
                    ui.label(title).classes("text-xs uppercase tracking-wider text-slate-500")
                    ui.label(value).classes("text-2xl font-semibold")
