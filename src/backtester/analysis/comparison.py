"""Multi-run comparison: trader score and ranking."""

from __future__ import annotations

from typing import Any


def compute_trader_score(metrics: dict[str, Any]) -> float:
    """Weighted composite score for run ranking.

    Components (weights sum to 1.0):
        - Profit factor (30%): capped at 5.0
        - Win rate (20%): raw fraction 0-1
        - Sharpe ratio (25%): floored at -2.0
        - Max drawdown (15%): penalty relative to $50k
        - Trade count (10%): penalizes < 30 trades
    """
    pf = min(float(metrics.get("profit_factor", 0) or 0), 5.0)
    wr = float(metrics.get("win_rate", 0) or 0)
    dd = abs(float(metrics.get("max_drawdown", 1) or 1))
    sharpe = max(float(metrics.get("sharpe", 0) or 0), -2.0)
    n = int(metrics.get("num_trades", 0) or 0)
    trade_penalty = min(n / 30, 1.0)
    score = pf * 0.30 + wr * 0.20 + sharpe * 0.25 + (1 - min(dd / 50_000, 1)) * 0.15 + trade_penalty * 0.10
    return round(score * 100, 1)


def rank_runs(
    runs: list[Any],
) -> list[tuple[Any, float]]:
    """Sort runs by trader score (descending). Each run must have a .metrics dict attribute."""
    scored = [(run, compute_trader_score(run.metrics or {})) for run in runs]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
