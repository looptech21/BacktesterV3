from __future__ import annotations

from pathlib import Path

from backtester.config import RunConfig


def test_run_config_includes_split_adjustment_defaults(tmp_path: Path) -> None:
    config = RunConfig.model_validate(
        {
            "run_name": "defaults_check",
            "period": {"start_date": "2020-01-01", "end_date": "2020-12-31"},
            "data": {"parquet_root": str(tmp_path / "parquet")},
            "universe": {"mode": "custom_list", "tickers": ["TSLA"]},
            "output": {"dir": str(tmp_path / "outputs")},
        }
    )

    assert config.data.source_layout == "symbol_year"
    assert config.data.lean_feed_scope == "full"
    assert config.execution.order_session_scope == "rth_only"
    assert config.data.split_adjustment_mode == "none"
    assert config.data.split_adjust_volume is True
    assert config.setups.episodic_pivot.premarket_gate_source_mode == "precomputed_preferred"
    assert str(config.data.split_events_file).endswith("hf_defeatbeta_stock_split_events_2026-02-16.parquet")


def test_run_config_accepts_split_adjustment_overrides(tmp_path: Path) -> None:
    split_events_file = tmp_path / "events.csv"

    config = RunConfig.model_validate(
        {
            "run_name": "override_check",
            "period": {"start_date": "2020-01-01", "end_date": "2020-12-31"},
            "data": {
                "parquet_root": str(tmp_path / "parquet"),
                "split_adjustment_mode": "splits_backward",
                "split_events_file": str(split_events_file),
                "split_adjust_volume": False,
                "source_layout": "symbol_year",
                "lean_feed_scope": "rth",
            },
            "execution": {"order_session_scope": "full"},
            "setups": {"episodic_pivot": {"premarket_gate_source_mode": "proxy_only"}},
            "universe": {"mode": "custom_list", "tickers": ["TSLA"]},
            "output": {"dir": str(tmp_path / "outputs")},
        }
    )

    assert config.data.split_adjustment_mode == "splits_backward"
    assert config.data.split_adjust_volume is False
    assert config.data.source_layout == "symbol_year"
    assert config.data.lean_feed_scope == "rth"
    assert config.execution.order_session_scope == "full"
    assert config.data.split_events_file == split_events_file
    assert config.setups.episodic_pivot.premarket_gate_source_mode == "proxy_only"
