from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from backtester.gui.services.market_data_service import MarketDataService


def _write_intraday_month(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def test_market_data_service_discovers_catalog_and_resolves_roots(tmp_path):
    base = tmp_path / "parquet"
    (base / "session=rth" / "timeframe=1m").mkdir(parents=True)
    (base / "session=rth" / "timeframe=5m").mkdir(parents=True)
    (base / "session=full" / "timeframe=1d").mkdir(parents=True)

    service = MarketDataService(parquet_base=base)
    catalog = service.discover_catalog()

    assert catalog["full"] == ["1d"]
    assert catalog["rth"] == ["1m", "5m"]
    assert service.resolve_parquet_root("rth", "1m") == base / "session=rth" / "timeframe=1m"
    assert service.resolve_parquet_root("rth", "15m") is None


def test_market_data_service_loads_intraday_bars_across_months(tmp_path):
    base = tmp_path / "parquet"
    root = base / "session=full" / "timeframe=5m"

    _write_intraday_month(
        root / "ohlcv_2020-01.parquet",
        [
            {
                "timestamp": "2020-01-31T15:30:00+00:00",
                "ticker": "AAPL",
                "open": 10.0,
                "high": 11.0,
                "low": 9.9,
                "close": 10.5,
                "volume": 1000,
                "trading_day_est": "2020-01-31",
            }
        ],
    )
    _write_intraday_month(
        root / "ohlcv_2020-02.parquet",
        [
            {
                "timestamp": "2020-02-03T15:30:00+00:00",
                "ticker": "AAPL",
                "open": 10.6,
                "high": 11.1,
                "low": 10.5,
                "close": 10.9,
                "volume": 1200,
                "trading_day_est": "2020-02-03",
            }
        ],
    )

    service = MarketDataService(parquet_base=base)
    bars = service.load_bars(
        symbol="AAPL",
        session="full",
        timeframe="5m",
        start=datetime(2020, 1, 31, 0, 0, tzinfo=UTC),
        end=datetime(2020, 2, 3, 23, 59, tzinfo=UTC),
    )

    assert list(bars.columns) == ["ts", "open", "high", "low", "close", "volume"]
    assert len(bars) == 2
    assert str(bars["ts"].dt.tz) == "America/New_York"


def test_market_data_service_normalizes_daily_bars(tmp_path):
    base = tmp_path / "parquet"
    root = base / "session=full" / "timeframe=1d"
    root.mkdir(parents=True)

    pd.DataFrame(
        [
            {
                "trading_day_est": "2020-01-02",
                "ticker": "TSLA",
                "open": 430.0,
                "high": 440.0,
                "low": 425.0,
                "close": 435.0,
                "volume": 100,
            },
            {
                "trading_day_est": "2020-01-03",
                "ticker": "TSLA",
                "open": 436.0,
                "high": 442.0,
                "low": 430.0,
                "close": 438.0,
                "volume": 120,
            },
        ]
    ).to_parquet(root / "ohlcv_2020-01.parquet", index=False)

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
