from __future__ import annotations

from backtester.analysis.metrics import compute_metrics_from_trades


def test_compute_metrics_includes_starting_balance_and_drawdown_pct() -> None:
    trades = [
        {
            "exit_fill_ts_utc": "2020-01-02T10:00:00+00:00",
            "entry_fill_ts_utc": "2020-01-02T09:31:00+00:00",
            "pnl_net": 1000.0,
        },
        {
            "exit_fill_ts_utc": "2020-01-03T10:00:00+00:00",
            "entry_fill_ts_utc": "2020-01-03T09:31:00+00:00",
            "pnl_net": -500.0,
        },
        {
            "exit_fill_ts_utc": "2020-01-06T10:00:00+00:00",
            "entry_fill_ts_utc": "2020-01-06T09:31:00+00:00",
            "pnl_net": -700.0,
        },
    ]

    metrics = compute_metrics_from_trades(trades, initial_cash=10_000.0)

    assert metrics["starting_balance"] == 10_000.0
    assert metrics["max_drawdown"] == 1_200.0
    assert abs(metrics["max_drawdown_pct"] - (1200.0 / 11_000.0)) < 1e-9


def test_compute_metrics_empty_includes_starting_balance() -> None:
    metrics = compute_metrics_from_trades([], initial_cash=50_000.0)
    assert metrics["starting_balance"] == 50_000.0
    assert metrics["max_drawdown_pct"] == 0.0
