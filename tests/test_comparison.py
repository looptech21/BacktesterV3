from __future__ import annotations

from backtester.analysis.comparison import compute_trader_score, rank_runs
from backtester.gui.state import RunSummary


def test_trader_score_basic():
    metrics = {
        "profit_factor": 2.0,
        "win_rate": 0.6,
        "sharpe": 1.5,
        "max_drawdown": 5000,
        "num_trades": 50,
    }
    score = compute_trader_score(metrics)
    assert score > 0
    assert isinstance(score, float)


def test_trader_score_zero_trades():
    metrics = {
        "profit_factor": 0,
        "win_rate": 0,
        "sharpe": 0,
        "max_drawdown": 0,
        "num_trades": 0,
    }
    score = compute_trader_score(metrics)
    # Trade penalty should reduce score (0/30 = 0)
    assert isinstance(score, float)

    metrics_with_trades = dict(metrics)
    metrics_with_trades["num_trades"] = 30
    score_with_trades = compute_trader_score(metrics_with_trades)
    assert score_with_trades > score


def test_trader_score_clamping():
    """Profit factor capped at 5, sharpe floored at -2."""
    metrics = {
        "profit_factor": 100.0,
        "win_rate": 1.0,
        "sharpe": 50.0,
        "max_drawdown": 0,
        "num_trades": 100,
    }
    score_extreme = compute_trader_score(metrics)

    metrics_capped = dict(metrics)
    metrics_capped["profit_factor"] = 5.0
    metrics_capped["sharpe"] = 50.0
    score_capped = compute_trader_score(metrics_capped)

    assert score_extreme == score_capped


def test_rank_runs():
    runs = [
        RunSummary(
            id=1,
            run_name="bad",
            experiment_id="a",
            status="ok",
            run_dir="/tmp/a",
            created_at="2026-01-01",
            metrics={"profit_factor": 0.5, "win_rate": 0.3, "sharpe": -0.5, "max_drawdown": 20000, "num_trades": 10},
        ),
        RunSummary(
            id=2,
            run_name="good",
            experiment_id="b",
            status="ok",
            run_dir="/tmp/b",
            created_at="2026-01-01",
            metrics={"profit_factor": 3.0, "win_rate": 0.7, "sharpe": 2.0, "max_drawdown": 2000, "num_trades": 50},
        ),
        RunSummary(
            id=3,
            run_name="mid",
            experiment_id="c",
            status="ok",
            run_dir="/tmp/c",
            created_at="2026-01-01",
            metrics={"profit_factor": 1.5, "win_rate": 0.5, "sharpe": 0.8, "max_drawdown": 8000, "num_trades": 30},
        ),
    ]
    ranked = rank_runs(runs)
    assert len(ranked) == 3
    assert ranked[0][0].run_name == "good"
    assert ranked[-1][0].run_name == "bad"
    # Scores should be descending
    assert ranked[0][1] >= ranked[1][1] >= ranked[2][1]
