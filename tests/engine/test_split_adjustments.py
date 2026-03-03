from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from backtester.engine.split_adjustments import (
    SplitAdjustmentError,
    SplitEvent,
    apply_backward_split_adjustments,
    load_split_events,
    parse_split_ratio,
)


def test_parse_split_ratio_valid_formats() -> None:
    assert parse_split_ratio("5:1") == pytest.approx(5.0)
    assert parse_split_ratio("1/3") == pytest.approx(1.0 / 3.0)
    assert parse_split_ratio("1398:1000") == pytest.approx(1.398)
    assert parse_split_ratio("6.0E-5:1") == pytest.approx(6.0e-5)


def test_parse_split_ratio_invalid_formats() -> None:
    for raw in ("", "abc", "0:1", "1:0", "-1:2"):
        with pytest.raises(ValueError):
            parse_split_ratio(raw)


def test_load_split_events_parses_and_filters(tmp_path) -> None:
    split_file = tmp_path / "split_events.parquet"
    pd.DataFrame(
        [
            {"symbol": "TSLA", "report_date": "2020-08-31", "split_factor": "5:1"},
            {"symbol": "TSLA", "report_date": "2022-08-25", "split_factor": "3:1"},
            {"symbol": "AAPL", "report_date": "2020-08-31", "split_factor": "4:1"},
            {"symbol": "XYZ", "report_date": "2020-01-10", "split_factor": "6.0E-5:1"},
        ]
    ).to_parquet(split_file, index=False)

    events = load_split_events(split_file, tickers={"TSLA", "XYZ"}, end_date=date(2020, 12, 31))

    assert set(events.keys()) == {"TSLA", "XYZ"}
    assert len(events["TSLA"]) == 1
    assert events["TSLA"][0].ex_date == date(2020, 8, 31)
    assert events["TSLA"][0].ratio_new_over_old == pytest.approx(5.0)
    assert events["XYZ"][0].ratio_new_over_old == pytest.approx(6.0e-5)


def test_load_split_events_missing_file_raises() -> None:
    with pytest.raises(SplitAdjustmentError):
        load_split_events(
            Path("/tmp/does-not-exist.parquet"),
            tickers={"TSLA"},
            end_date=date(2020, 12, 31),
        )


def test_apply_backward_split_adjustments_single_split_scales_prices_and_volume() -> None:
    frame = pd.DataFrame(
        {
            "ticker": ["TSLA", "TSLA"],
            "open": [2000.0, 500.0],
            "high": [2100.0, 510.0],
            "low": [1900.0, 490.0],
            "close": [2050.0, 505.0],
            "volume": [100.0, 500.0],
        }
    )
    local_day = pd.Series([date(2020, 8, 28), date(2020, 8, 31)])
    split_events = {"TSLA": [SplitEvent("TSLA", date(2020, 8, 31), 5.0)]}

    adjusted, symbols_with_splits, rows_adjusted = apply_backward_split_adjustments(
        frame,
        symbol_col="ticker",
        local_day=local_day,
        split_events=split_events,
        adjust_volume=True,
    )

    assert symbols_with_splits == 1
    assert rows_adjusted == 1
    assert adjusted.loc[0, "open"] == pytest.approx(400.0)
    assert adjusted.loc[0, "close"] == pytest.approx(410.0)
    assert adjusted.loc[0, "volume"] == pytest.approx(500.0)
    assert adjusted.loc[1, "open"] == pytest.approx(500.0)
    assert adjusted.loc[1, "volume"] == pytest.approx(500.0)


def test_apply_backward_split_adjustments_multiple_splits_and_volume_toggle() -> None:
    frame = pd.DataFrame(
        {
            "ticker": ["TSLA", "TSLA", "TSLA"],
            "open": [1500.0, 900.0, 300.0],
            "high": [1500.0, 900.0, 300.0],
            "low": [1500.0, 900.0, 300.0],
            "close": [1500.0, 900.0, 300.0],
            "volume": [10.0, 20.0, 30.0],
        }
    )
    local_day = pd.Series([date(2020, 8, 28), date(2021, 1, 4), date(2023, 1, 3)])
    split_events = {
        "TSLA": [
            SplitEvent("TSLA", date(2020, 8, 31), 5.0),
            SplitEvent("TSLA", date(2022, 8, 25), 3.0),
        ]
    }

    adjusted, _, _ = apply_backward_split_adjustments(
        frame,
        symbol_col="ticker",
        local_day=local_day,
        split_events=split_events,
        adjust_volume=False,
    )

    assert adjusted["close"].tolist() == pytest.approx([100.0, 300.0, 300.0])
    assert adjusted["volume"].tolist() == pytest.approx([10.0, 20.0, 30.0])
