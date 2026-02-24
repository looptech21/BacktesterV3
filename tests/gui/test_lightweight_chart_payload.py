from __future__ import annotations

import pandas as pd

from backtester.gui.components.lightweight_chart import bars_from_frame, markers_from_internal_markers


def test_bars_from_frame_converts_to_utc_epoch_seconds():
    ts_ny = pd.Timestamp("2020-01-02 09:30:00", tz="America/New_York")
    frame = pd.DataFrame(
        [
            {
                "ts": ts_ny,
                "open": 100.0,
                "high": 102.0,
                "low": 99.5,
                "close": 101.25,
                "volume": 1000,
            }
        ]
    )

    bars = bars_from_frame(frame)

    assert len(bars) == 1
    assert bars[0]["time"] == int(ts_ny.tz_convert("UTC").timestamp())
    assert bars[0]["open"] == 100.0
    assert bars[0]["high"] == 102.0
    assert bars[0]["low"] == 99.5
    assert bars[0]["close"] == 101.25


def test_markers_from_internal_markers_preserves_exact_price_position():
    markers = [
        {
            "label": "Entry",
            "x": pd.Timestamp("2020-01-02 09:31:00", tz="America/New_York"),
            "y": 101.25,
            "color": "#16a34a",
            "symbol": "triangle-up",
        },
        {
            "label": "Exit",
            "x": pd.Timestamp("2020-01-03 15:45:00", tz="America/New_York"),
            "y": 104.5,
            "color": "#dc2626",
            "symbol": "triangle-down",
        },
    ]

    payload = markers_from_internal_markers(markers)

    assert len(payload) == 2
    assert payload[0]["shape"] == "arrowUp"
    assert payload[0]["position"]["type"] == "price"
    assert payload[0]["position"]["price"] == 101.25
    assert payload[1]["shape"] == "arrowDown"
    assert payload[1]["position"]["type"] == "price"
    assert payload[1]["position"]["price"] == 104.5


def test_markers_from_internal_markers_adds_interpolated_logical_for_intraday():
    bars_frame = pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2020-01-02 09:30:00", tz="America/New_York"),
                "open": 100.0,
                "high": 101.0,
                "low": 99.5,
                "close": 100.5,
                "volume": 1000,
            },
            {
                "ts": pd.Timestamp("2020-01-02 09:35:00", tz="America/New_York"),
                "open": 100.5,
                "high": 101.5,
                "low": 100.0,
                "close": 101.0,
                "volume": 1000,
            },
        ]
    )
    bars = bars_from_frame(bars_frame)
    markers = [
        {
            "label": "Entry",
            "x": pd.Timestamp("2020-01-02 09:32:00", tz="America/New_York"),
            "y": 100.9,
            "color": "#16a34a",
            "symbol": "triangle-up",
        }
    ]

    payload = markers_from_internal_markers(markers, bars=bars, timeframe="5m")

    assert len(payload) == 1
    assert payload[0]["logical"] == 0.4


def test_markers_from_internal_markers_snaps_daily_to_trading_day_candle():
    bars_frame = pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2020-01-02 00:00:00", tz="America/New_York"),
                "open": 100.0,
                "high": 101.0,
                "low": 99.5,
                "close": 100.5,
                "volume": 1000,
            },
            {
                "ts": pd.Timestamp("2020-01-03 00:00:00", tz="America/New_York"),
                "open": 101.0,
                "high": 102.0,
                "low": 100.0,
                "close": 101.5,
                "volume": 1000,
            },
        ]
    )
    bars = bars_from_frame(bars_frame)
    markers = [
        {
            "label": "Entry",
            "x": pd.Timestamp("2020-01-02 15:31:00", tz="America/New_York"),
            "y": 100.9,
            "color": "#16a34a",
            "symbol": "triangle-up",
        }
    ]

    payload = markers_from_internal_markers(markers, bars=bars, timeframe="1d")

    assert len(payload) == 1
    assert payload[0]["logical"] == 0.0


def test_markers_from_internal_markers_omits_logical_when_outside_intraday_window():
    bars_frame = pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2020-01-02 09:30:00", tz="America/New_York"),
                "open": 100.0,
                "high": 101.0,
                "low": 99.5,
                "close": 100.5,
                "volume": 1000,
            },
            {
                "ts": pd.Timestamp("2020-01-02 09:35:00", tz="America/New_York"),
                "open": 100.5,
                "high": 101.5,
                "low": 100.0,
                "close": 101.0,
                "volume": 1000,
            },
        ]
    )
    bars = bars_from_frame(bars_frame)
    markers = [
        {
            "label": "Entry",
            "x": pd.Timestamp("2020-01-02 09:20:00", tz="America/New_York"),
            "y": 100.9,
            "color": "#16a34a",
            "symbol": "triangle-up",
        }
    ]

    payload = markers_from_internal_markers(markers, bars=bars, timeframe="5m")

    assert len(payload) == 1
    assert "logical" not in payload[0]
