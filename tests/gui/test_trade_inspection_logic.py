from __future__ import annotations

import pandas as pd

from backtester.gui.components.lightweight_chart import bars_from_frame, markers_from_internal_markers
from backtester.gui.components.trade_inspection import extract_trade_markers
from backtester.gui.services.market_data_service import MarketDataService


def test_extract_trade_markers_returns_exact_entry_exit_points():
    trade = {
        "entry_fill_ts_utc": "2020-01-02T14:31:00+00:00",
        "entry_price": 101.25,
        "exit_fill_ts_utc": "2020-01-03T15:45:00+00:00",
        "exit_price": 104.5,
    }

    markers, warnings = extract_trade_markers(trade)

    assert warnings == []
    assert len(markers) == 2
    assert markers[0]["label"] == "Entry"
    assert markers[0]["y"] == 101.25
    assert markers[1]["label"] == "Exit"
    assert markers[1]["y"] == 104.5
    assert str(markers[0]["x"].tz) == "America/New_York"


def test_make_trade_windows_for_closed_trade():
    windows = MarketDataService.make_trade_windows(
        "2020-01-02T14:31:00+00:00",
        "2020-01-03T20:00:00+00:00",
    )

    daily_start, daily_end = windows["daily"]
    granular_start, granular_end = windows["granular"]

    assert daily_start < daily_end
    assert granular_start.date().isoformat() == "2020-01-01"
    assert granular_end.date().isoformat() == "2020-01-04"


def test_make_trade_windows_for_open_trade():
    windows = MarketDataService.make_trade_windows(
        "2020-01-02T14:31:00+00:00",
        None,
    )

    granular_start, granular_end = windows["granular"]
    assert granular_start.date().isoformat() == "2020-01-01"
    assert granular_end.date().isoformat() == "2020-01-03"


def test_trade_markers_convert_to_lightweight_payload_shape_and_price():
    internal_markers = [
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

    payload = markers_from_internal_markers(internal_markers)

    assert payload[0]["shape"] == "arrowUp"
    assert payload[0]["position"]["type"] == "price"
    assert payload[0]["position"]["price"] == 101.25
    assert payload[1]["shape"] == "arrowDown"
    assert payload[1]["position"]["price"] == 104.5


def test_trade_markers_compute_logical_positions_with_bar_context():
    bars_frame = pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2020-01-02 09:30:00", tz="America/New_York"),
                "open": 100.0,
                "high": 101.0,
                "low": 99.5,
                "close": 100.5,
                "volume": 100,
            },
            {
                "ts": pd.Timestamp("2020-01-02 09:35:00", tz="America/New_York"),
                "open": 100.5,
                "high": 101.5,
                "low": 100.0,
                "close": 101.0,
                "volume": 100,
            },
        ]
    )
    bars = bars_from_frame(bars_frame)
    internal_markers = [
        {
            "label": "Entry",
            "x": pd.Timestamp("2020-01-02 09:32:00", tz="America/New_York"),
            "y": 101.25,
            "color": "#16a34a",
            "symbol": "triangle-up",
        }
    ]

    payload = markers_from_internal_markers(internal_markers, bars=bars, timeframe="5m")

    assert payload
    assert payload[0]["logical"] == 0.4


def test_bar_frame_conversion_to_lightweight_payload_keeps_ohlc():
    ts = pd.Timestamp("2020-01-02 09:30:00", tz="America/New_York")
    frame = pd.DataFrame(
        [
            {
                "ts": ts,
                "open": 100.0,
                "high": 102.0,
                "low": 99.5,
                "close": 101.25,
                "volume": 100,
            }
        ]
    )

    bars = bars_from_frame(frame)

    assert len(bars) == 1
    assert bars[0]["open"] == 100.0
    assert bars[0]["high"] == 102.0
    assert bars[0]["low"] == 99.5
    assert bars[0]["close"] == 101.25
