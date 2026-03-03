from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from backtester.config import RunConfig
from backtester.engine.algorithm.QM_MVP import QM_MVP
from backtester.engine.algorithm.exit_engine import (
    PositionSnapshot,
    evaluate_intraday_stop,
    evaluate_partial_exit,
)


def _make_config(parquet_root: str) -> RunConfig:
    return RunConfig.model_validate(
        {
            "run_name": "algo_helper_test",
            "period": {"start_date": "2024-01-01", "end_date": "2024-01-31"},
            "data": {"parquet_root": parquet_root},
            "universe": {"mode": "custom_list", "tickers": ["TSLA"]},
            "output": {"dir": "/tmp/backtester_v3_test"},
        }
    )


def test_bar_step_minutes_resolved_from_parquet_root() -> None:
    cases = {
        "/tmp/dataset/bars/1m": 1,
        "/tmp/dataset/bars/5m": 5,
        "/tmp/dataset/bars/15m": 15,
        "/tmp/dataset/bars/1h": 60,
        "/tmp/dataset/unknown": 1,
    }
    for root, expected in cases.items():
        algo = QM_MVP.__new__(QM_MVP)
        algo.run_config = _make_config(root)
        assert algo._resolve_bar_step_minutes() == expected


def test_rth_gate_boundaries() -> None:
    algo = QM_MVP.__new__(QM_MVP)
    algo._rth_start_minute = 9 * 60 + 30
    algo._rth_end_minute = 16 * 60

    assert algo._is_rth_time(datetime(2024, 1, 2, 9, 29), include_end=False) is False
    assert algo._is_rth_time(datetime(2024, 1, 2, 9, 30), include_end=False) is True
    assert algo._is_rth_time(datetime(2024, 1, 2, 15, 59), include_end=False) is True
    assert algo._is_rth_time(datetime(2024, 1, 2, 16, 0), include_end=False) is False
    assert algo._is_rth_time(datetime(2024, 1, 2, 16, 0), include_end=True) is True


def test_intent_delay_uses_bar_step() -> None:
    algo = QM_MVP.__new__(QM_MVP)
    algo._bar_step_minutes = 15
    assert algo._intent_delay() == timedelta(minutes=15)

    algo._bar_step_minutes = 0
    assert algo._intent_delay() == timedelta(minutes=1)


def test_exit_engine_intraday_stop_uses_bar_step_minutes() -> None:
    position = PositionSnapshot(
        experiment_id="exp",
        symbol="TSLA",
        qty_open=100,
        entry_avg_price=100.0,
        entry_day=date(2024, 1, 2),
        setup_type="common_breakout",
        initial_stop=95.0,
        dynamic_stop=98.0,
        partial_done=False,
        scan_day="2024-01-02",
        trade_day="2024-01-02",
        trailing_ma_days=10,
    )
    bar = SimpleNamespace(Low=97.5)
    now_utc = datetime(2024, 1, 3, 15, 35, tzinfo=timezone.utc)

    intent = evaluate_intraday_stop(position, bar, now_utc, bar_step_minutes=5)
    assert intent is not None
    assert intent.signal_ts_utc == now_utc
    assert intent.earliest_exec_ts_utc == now_utc + timedelta(minutes=5)


def test_exit_engine_partial_exit_uses_bar_step_minutes() -> None:
    position = PositionSnapshot(
        experiment_id="exp",
        symbol="TSLA",
        qty_open=90,
        entry_avg_price=100.0,
        entry_day=date(2024, 1, 1),
        setup_type="common_breakout",
        initial_stop=95.0,
        dynamic_stop=98.0,
        partial_done=False,
        scan_day="2024-01-01",
        trade_day="2024-01-01",
        trailing_ma_days=10,
    )
    now_est = datetime(2024, 1, 8, 14, 30, tzinfo=timezone.utc)
    config = SimpleNamespace(partial_exit_day=3, partial_exit_fraction=0.5, partial_exit_time="any_bar")

    intent = evaluate_partial_exit(position, now_est, config, bar_step_minutes=15)
    assert intent is not None
    assert intent.earliest_exec_ts_utc == intent.signal_ts_utc + timedelta(minutes=15)
