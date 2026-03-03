from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from backtester.gui.services.market_data_service import MarketDataService


def _write_year(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def test_market_data_service_discovers_catalog_and_resolves_roots(tmp_path):
    base = tmp_path / "bars"
    _write_year(base / "5m" / "symbol=AAPL" / "year=2024.parquet", [{"symbol": "AAPL", "ts_utc": "2024-01-02T14:30:00+00:00", "session_date": "2024-01-02", "is_rth": True, "is_premarket": False, "is_postmarket": False, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 100.0}])
    _write_year(base / "1d" / "symbol=AAPL" / "year=2024.parquet", [{"symbol": "AAPL", "session_date": "2024-01-02", "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 100.0}])

    service = MarketDataService(parquet_base=base)
    catalog = service.discover_catalog()

    assert catalog["full"] == ["5m", "1d"]
    assert catalog["rth"] == ["5m", "1d"]
    assert service.resolve_parquet_root("rth", "5m") == base / "5m"
    assert service.resolve_parquet_root("full", "15m") is None


def test_market_data_service_loads_intraday_bars_across_years(tmp_path):
    base = tmp_path / "bars"

    _write_year(
        base / "5m" / "symbol=AAPL" / "year=2023.parquet",
        [
            {
                "symbol": "AAPL",
                "ts_utc": "2023-12-29T15:30:00+00:00",
                "session_date": "2023-12-29",
                "is_rth": True,
                "is_premarket": False,
                "is_postmarket": False,
                "open": 10.0,
                "high": 11.0,
                "low": 9.9,
                "close": 10.5,
                "volume": 1000,
            }
        ],
    )
    _write_year(
        base / "5m" / "symbol=AAPL" / "year=2024.parquet",
        [
            {
                "symbol": "AAPL",
                "ts_utc": "2024-01-02T15:30:00+00:00",
                "session_date": "2024-01-02",
                "is_rth": True,
                "is_premarket": False,
                "is_postmarket": False,
                "open": 10.6,
                "high": 11.1,
                "low": 10.5,
                "close": 10.9,
                "volume": 1200,
            }
        ],
    )

    service = MarketDataService(parquet_base=base)
    bars = service.load_bars(
        symbol="AAPL",
        session="full",
        timeframe="5m",
        start=datetime(2023, 12, 29, 0, 0, tzinfo=UTC),
        end=datetime(2024, 1, 2, 23, 59, tzinfo=UTC),
    )

    assert list(bars.columns) == ["ts", "open", "high", "low", "close", "volume"]
    assert len(bars) == 2
    assert str(bars["ts"].dt.tz) == "America/New_York"


def test_market_data_service_intraday_session_filters(tmp_path):
    base = tmp_path / "bars"
    _write_year(
        base / "5m" / "symbol=TSLA" / "year=2024.parquet",
        [
            {
                "symbol": "TSLA",
                "ts_utc": "2024-01-02T13:00:00+00:00",
                "session_date": "2024-01-02",
                "is_rth": False,
                "is_premarket": True,
                "is_postmarket": False,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 100,
            },
            {
                "symbol": "TSLA",
                "ts_utc": "2024-01-02T15:00:00+00:00",
                "session_date": "2024-01-02",
                "is_rth": True,
                "is_premarket": False,
                "is_postmarket": False,
                "open": 101.0,
                "high": 102.0,
                "low": 100.0,
                "close": 101.5,
                "volume": 200,
            },
            {
                "symbol": "TSLA",
                "ts_utc": "2024-01-02T22:00:00+00:00",
                "session_date": "2024-01-02",
                "is_rth": False,
                "is_premarket": False,
                "is_postmarket": True,
                "open": 102.0,
                "high": 103.0,
                "low": 101.5,
                "close": 102.2,
                "volume": 150,
            },
        ],
    )

    service = MarketDataService(parquet_base=base)
    start = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)
    end = datetime(2024, 1, 3, 0, 0, tzinfo=UTC)

    assert len(service.load_bars("TSLA", "full", "5m", start, end)) == 3
    assert len(service.load_bars("TSLA", "premarket", "5m", start, end)) == 1
    assert len(service.load_bars("TSLA", "rth", "5m", start, end)) == 1
    assert len(service.load_bars("TSLA", "postmarket", "5m", start, end)) == 1


def test_market_data_service_normalizes_daily_bars(tmp_path):
    base = tmp_path / "bars"

    _write_year(
        base / "1d" / "symbol=TSLA" / "year=2020.parquet",
        [
            {
                "symbol": "TSLA",
                "session_date": "2020-01-02",
                "open": 430.0,
                "high": 440.0,
                "low": 425.0,
                "close": 435.0,
                "volume": 100,
            },
            {
                "symbol": "TSLA",
                "session_date": "2020-01-03",
                "open": 436.0,
                "high": 442.0,
                "low": 430.0,
                "close": 438.0,
                "volume": 120,
            },
        ],
    )

    service = MarketDataService(parquet_base=base)
    bars = service.load_bars(
        symbol="TSLA",
        session="full",
        timeframe="1d",
        start=datetime(2020, 1, 1, 0, 0, tzinfo=UTC),
        end=datetime(2020, 1, 10, 0, 0, tzinfo=UTC),
    )

    assert len(bars) == 2
    assert bars.iloc[0]["ts"].hour == 0
    assert bars.iloc[0]["ts"].minute == 0
    assert str(bars["ts"].dt.tz) == "America/New_York"


def test_market_data_service_applies_split_adjustment_with_end_date_horizon(tmp_path):
    base = tmp_path / "bars"

    _write_year(
        base / "1d" / "symbol=TSLA" / "year=2020.parquet",
        [
            {
                "symbol": "TSLA",
                "session_date": "2020-08-28",
                "open": 2000.0,
                "high": 2020.0,
                "low": 1980.0,
                "close": 2010.0,
                "volume": 100.0,
            },
            {
                "symbol": "TSLA",
                "session_date": "2020-08-31",
                "open": 500.0,
                "high": 510.0,
                "low": 490.0,
                "close": 505.0,
                "volume": 500.0,
            },
        ],
    )

    split_file = tmp_path / "split_events.parquet"
    pd.DataFrame(
        [
            {"symbol": "TSLA", "report_date": "2020-08-31", "split_factor": "5:1"},
            {"symbol": "TSLA", "report_date": "2022-08-25", "split_factor": "3:1"},
        ]
    ).to_parquet(split_file, index=False)

    service = MarketDataService(parquet_base=base)
    service.configure_split_adjustment(
        mode="splits_backward",
        split_events_file=split_file,
        adjust_volume=True,
        end_date=date(2020, 12, 31),
        timezone="America/New_York",
    )

    bars_2020_horizon = service.load_bars(
        symbol="TSLA",
        session="full",
        timeframe="1d",
        start=datetime(2020, 8, 1, 0, 0, tzinfo=UTC),
        end=datetime(2020, 9, 5, 0, 0, tzinfo=UTC),
    )

    assert len(bars_2020_horizon) == 2
    assert bars_2020_horizon.iloc[0]["open"] == pytest.approx(400.0)
    assert bars_2020_horizon.iloc[0]["volume"] == pytest.approx(500.0)

    service.configure_split_adjustment(
        mode="splits_backward",
        split_events_file=split_file,
        adjust_volume=True,
        end_date=date(2023, 12, 31),
        timezone="America/New_York",
    )
    bars_2023_horizon = service.load_bars(
        symbol="TSLA",
        session="full",
        timeframe="1d",
        start=datetime(2020, 8, 1, 0, 0, tzinfo=UTC),
        end=datetime(2020, 9, 5, 0, 0, tzinfo=UTC),
    )

    assert len(bars_2023_horizon) == 2
    assert bars_2023_horizon.iloc[0]["open"] == pytest.approx(2000.0 / 15.0)
    assert bars_2023_horizon.iloc[0]["volume"] == pytest.approx(1500.0)
