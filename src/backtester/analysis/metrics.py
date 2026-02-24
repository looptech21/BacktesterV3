"""Backtest metrics computation.

Ported from BacktesterV2/reporting/metrics.py.
Computes PnL, win rate, profit factor, Sharpe, CAGR, drawdown, exposure, and turnover.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _parse_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


def _compute_exposure(
    closed: list[dict[str, Any]],
    initial_cash: float,
    total_minutes: float,
) -> float:
    if initial_cash <= 0 or total_minutes <= 0:
        return 0.0
    weighted_sum = 0.0
    for t in closed:
        entry_price = _safe_float(t.get("entry_price"))
        entry_qty = _safe_float(t.get("entry_qty"))
        holding = _safe_float(t.get("holding_minutes"))
        if entry_price > 0 and entry_qty > 0 and holding > 0:
            weighted_sum += entry_price * entry_qty * holding
    return weighted_sum / (initial_cash * total_minutes)


def _compute_turnover(closed: list[dict[str, Any]], initial_cash: float) -> float:
    if initial_cash <= 0:
        return 0.0
    total_value = sum(
        _safe_float(t.get("entry_price")) * _safe_float(t.get("entry_qty"))
        for t in closed
        if _safe_float(t.get("entry_price")) > 0 and _safe_float(t.get("entry_qty")) > 0
    )
    return total_value / initial_cash


def _compute_sharpe(
    pnl_net_values: list[float],
    initial_cash: float,
    years: float,
    risk_free_rate: float = 0.0,
) -> float:
    n = len(pnl_net_values)
    if n < 2 or initial_cash <= 0 or years <= 0:
        return 0.0
    trades_per_year = n / years
    rf_per_trade = risk_free_rate / trades_per_year if trades_per_year > 0 else 0.0
    returns = [p / initial_cash for p in pnl_net_values]
    excess = [r - rf_per_trade for r in returns]
    mean_excess = sum(excess) / n
    variance = sum((r - mean_excess) ** 2 for r in excess) / (n - 1)
    stdev = math.sqrt(variance)
    if stdev <= 0:
        return 0.0
    return (mean_excess / stdev) * math.sqrt(trades_per_year)


def _compute_cagr(net_pnl: float, initial_cash: float, years: float) -> float:
    if initial_cash <= 0 or years <= 0:
        return 0.0
    return (1.0 + net_pnl / initial_cash) ** (1.0 / years) - 1.0


def compute_metrics_from_trades(
    trades: list[dict[str, Any]],
    *,
    initial_cash: float = 100_000.0,
    risk_free_rate: float = 0.0,
) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "num_trades": 0,
        "num_trades_closed": 0,
        "starting_balance": initial_cash,
        "gross_pnl": 0.0,
        "net_pnl": 0.0,
        "avg_pnl": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "max_drawdown": 0.0,
        "max_drawdown_pct": 0.0,
        "cagr": 0.0,
        "sharpe": 0.0,
        "exposure": 0.0,
        "turnover": 0.0,
        "equity_curve": [],
    }
    if not trades:
        return empty

    closed = [t for t in trades if t.get("exit_fill_ts_utc")]
    if not closed:
        closed = trades

    pnl_gross_values = [_safe_float(t.get("pnl_gross", t.get("pnl", 0.0))) for t in closed]
    pnl_net_values = [_safe_float(t.get("pnl_net", t.get("pnl", 0.0))) for t in closed]
    gross_pnl = sum(pnl_gross_values)
    net_pnl = sum(pnl_net_values)
    avg_pnl = net_pnl / len(pnl_net_values)
    wins = sum(1 for p in pnl_net_values if p > 0)
    gross_wins = sum(p for p in pnl_net_values if p > 0)
    gross_losses = abs(sum(p for p in pnl_net_values if p < 0))
    win_rate = wins / len(pnl_net_values)
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else (gross_wins if gross_wins > 0 else 0.0)

    # Equity curve & max drawdown
    running = 0.0
    curve: list[float] = []
    peak_equity = initial_cash if initial_cash > 0 else 0.0
    max_dd = 0.0
    max_dd_pct = 0.0
    for p in pnl_net_values:
        running += p
        equity = initial_cash + running
        peak_equity = max(peak_equity, equity)
        dd = peak_equity - equity
        max_dd = max(max_dd, dd)
        if peak_equity > 0:
            max_dd_pct = max(max_dd_pct, dd / peak_equity)
        curve.append(running)

    # Time span
    entry_timestamps: list[float] = []
    exit_timestamps: list[float] = []
    for trade in closed:
        entry_dt = _parse_ts(trade.get("entry_fill_ts_utc"))
        if entry_dt is not None:
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=timezone.utc)
            entry_timestamps.append(entry_dt.timestamp())
        exit_dt = _parse_ts(trade.get("exit_fill_ts_utc"))
        if exit_dt is not None:
            if exit_dt.tzinfo is None:
                exit_dt = exit_dt.replace(tzinfo=timezone.utc)
            exit_timestamps.append(exit_dt.timestamp())

    years = 0.0
    total_minutes = 0.0
    if entry_timestamps:
        start_ts = min(entry_timestamps)
        end_ts = max(exit_timestamps) if exit_timestamps else max(entry_timestamps)
        if end_ts > start_ts:
            delta_seconds = end_ts - start_ts
            years = max(delta_seconds / (365.25 * 86400), 1 / 365.25)
            total_minutes = max(delta_seconds / 60.0, 1.0)

    return {
        "num_trades": len(closed),
        "num_trades_closed": len(closed),
        "starting_balance": initial_cash,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "avg_pnl": avg_pnl,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "cagr": _compute_cagr(net_pnl, initial_cash, years),
        "sharpe": _compute_sharpe(pnl_net_values, initial_cash, years, risk_free_rate),
        "exposure": _compute_exposure(closed, initial_cash, total_minutes),
        "turnover": _compute_turnover(closed, initial_cash),
        "equity_curve": curve,
    }
