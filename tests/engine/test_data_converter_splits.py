from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from backtester.engine.data_converter import ConversionConfig, convert_parquet_to_lean


def _read_zip_rows(zip_path: Path) -> list[list[int]]:
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.namelist()
        assert len(members) == 1
        with zf.open(members[0]) as fh:
            reader = csv.reader(io.TextIOWrapper(fh, encoding="utf-8"))
            return [[int(value) for value in row] for row in reader]


def _write_symbol_year_file(root: Path, symbol: str, year: int, rows: list[dict]) -> None:
    symbol_dir = root / f"symbol={symbol}"
    symbol_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(symbol_dir / f"year={year}.parquet", index=False)


def _default_cfg(tmp_path: Path, parquet_root: Path, split_mode: str = "none") -> ConversionConfig:
    return ConversionConfig(
        parquet_root=parquet_root,
        lean_data_root=tmp_path / "lean_data",
        cache_root=tmp_path / "cache",
        tickers=["TSLA"],
        start_date=date(2020, 8, 1),
        end_date=date(2024, 12, 31),
        timezone="America/New_York",
        rth_start="09:30",
        rth_end="16:00",
        source_layout="symbol_year",
        lean_feed_scope="full",
        split_adjustment_mode=split_mode,
        split_events_file=tmp_path / "split_events.parquet",
        split_adjust_volume=True,
    )


def test_convert_parquet_to_lean_split_signature_behavior_symbol_year(tmp_path: Path) -> None:
    parquet_root = tmp_path / "bars_5m"
    _write_symbol_year_file(
        parquet_root,
        symbol="TSLA",
        year=2020,
        rows=[
            {
                "symbol": "TSLA",
                "ts_utc": "2020-08-28T13:31:00+00:00",
                "session_date": "2020-08-28",
                "is_rth": True,
                "is_premarket": False,
                "is_postmarket": False,
                "open": 2000.0,
                "high": 2020.0,
                "low": 1980.0,
                "close": 2010.0,
                "volume": 100.0,
            },
            {
                "symbol": "TSLA",
                "ts_utc": "2020-08-31T13:31:00+00:00",
                "session_date": "2020-08-31",
                "is_rth": True,
                "is_premarket": False,
                "is_postmarket": False,
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

    cfg = ConversionConfig(
        parquet_root=parquet_root,
        lean_data_root=tmp_path / "lean_data",
        cache_root=tmp_path / "cache",
        tickers=["TSLA"],
        start_date=date(2020, 8, 1),
        end_date=date(2020, 9, 30),
        timezone="America/New_York",
        rth_start="09:30",
        rth_end="16:00",
        source_layout="symbol_year",
        lean_feed_scope="full",
        split_adjustment_mode="splits_backward",
        split_events_file=split_file,
        split_adjust_volume=True,
    )

    report_first = convert_parquet_to_lean(cfg)
    assert report_first.converted_files == 2
    assert report_first.skipped_files == 0
    assert report_first.rows_written == 2
    assert report_first.split_events_loaded == 1
    assert report_first.symbols_with_splits == 1
    assert report_first.rows_adjusted == 1

    zip_pre_split = cfg.lean_data_root / "equity" / "usa" / "minute" / "tsla" / "20200828_trade.zip"
    zip_post_split = cfg.lean_data_root / "equity" / "usa" / "minute" / "tsla" / "20200831_trade.zip"
    assert zip_pre_split.exists()
    assert zip_post_split.exists()

    row_pre = _read_zip_rows(zip_pre_split)[0]
    row_post = _read_zip_rows(zip_post_split)[0]
    assert row_pre[1:5] == [4_000_000, 4_040_000, 3_960_000, 4_020_000]
    assert row_pre[5] == 500
    assert row_post[1:5] == [5_000_000, 5_100_000, 4_900_000, 5_050_000]
    assert row_post[5] == 500

    meta_pre = Path(f"{zip_pre_split}.meta.json")
    meta_payload = json.loads(meta_pre.read_text(encoding="utf-8"))
    assert meta_payload["split_mode"] == "splits_backward"
    assert meta_payload["source_layout"] == "symbol_year"
    assert meta_payload["lean_feed_scope"] == "full"

    report_second = convert_parquet_to_lean(cfg)
    assert report_second.converted_files == 0
    assert report_second.skipped_files == 2


def test_convert_parquet_to_lean_feed_scope_filters_and_invalidates_signature(tmp_path: Path) -> None:
    parquet_root = tmp_path / "bars_5m"
    _write_symbol_year_file(
        parquet_root,
        symbol="TSLA",
        year=2024,
        rows=[
            {
                "symbol": "TSLA",
                "ts_utc": "2024-01-02T13:00:00+00:00",  # premarket
                "session_date": "2024-01-02",
                "is_rth": False,
                "is_premarket": True,
                "is_postmarket": False,
                "open": 95.0,
                "high": 96.0,
                "low": 94.5,
                "close": 95.5,
                "volume": 1000.0,
            },
            {
                "symbol": "TSLA",
                "ts_utc": "2024-01-02T14:30:00+00:00",  # 09:30 ET
                "session_date": "2024-01-02",
                "is_rth": True,
                "is_premarket": False,
                "is_postmarket": False,
                "open": 100.0,
                "high": 101.0,
                "low": 99.5,
                "close": 100.5,
                "volume": 200.0,
            },
        ],
    )

    cfg_full = ConversionConfig(
        parquet_root=parquet_root,
        lean_data_root=tmp_path / "lean_data",
        cache_root=tmp_path / "cache",
        tickers=["TSLA"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        timezone="America/New_York",
        rth_start="09:30",
        rth_end="16:00",
        source_layout="symbol_year",
        lean_feed_scope="full",
        split_adjustment_mode="none",
        split_events_file=tmp_path / "unused.parquet",
        split_adjust_volume=True,
    )

    report_full = convert_parquet_to_lean(cfg_full)
    assert report_full.converted_files == 1
    zip_path = cfg_full.lean_data_root / "equity" / "usa" / "minute" / "tsla" / "20240102_trade.zip"
    rows_full = _read_zip_rows(zip_path)
    assert len(rows_full) == 2

    cfg_rth = ConversionConfig(
        parquet_root=parquet_root,
        lean_data_root=cfg_full.lean_data_root,
        cache_root=cfg_full.cache_root,
        tickers=["TSLA"],
        start_date=cfg_full.start_date,
        end_date=cfg_full.end_date,
        timezone=cfg_full.timezone,
        rth_start=cfg_full.rth_start,
        rth_end=cfg_full.rth_end,
        source_layout="symbol_year",
        lean_feed_scope="rth",
        split_adjustment_mode="none",
        split_events_file=cfg_full.split_events_file,
        split_adjust_volume=True,
    )
    report_rth = convert_parquet_to_lean(cfg_rth)
    assert report_rth.converted_files == 1
    assert report_rth.skipped_files == 0

    rows_rth = _read_zip_rows(zip_path)
    assert len(rows_rth) == 1
    assert rows_rth[0][0] == 34_200_000

    meta_payload = json.loads(Path(f"{zip_path}.meta.json").read_text(encoding="utf-8"))
    assert meta_payload["lean_feed_scope"] == "rth"


def test_convert_parquet_to_lean_raises_when_split_file_missing(tmp_path: Path) -> None:
    parquet_root = tmp_path / "bars_5m"
    _write_symbol_year_file(
        parquet_root,
        symbol="TSLA",
        year=2020,
        rows=[
            {
                "symbol": "TSLA",
                "ts_utc": "2020-08-28T13:31:00+00:00",
                "session_date": "2020-08-28",
                "is_rth": True,
                "is_premarket": False,
                "is_postmarket": False,
                "open": 2000.0,
                "high": 2020.0,
                "low": 1980.0,
                "close": 2010.0,
                "volume": 100.0,
            }
        ],
    )

    cfg = ConversionConfig(
        parquet_root=parquet_root,
        lean_data_root=tmp_path / "lean_data",
        cache_root=tmp_path / "cache",
        tickers=["TSLA"],
        start_date=date(2020, 8, 1),
        end_date=date(2020, 8, 31),
        timezone="America/New_York",
        rth_start="09:30",
        rth_end="16:00",
        source_layout="symbol_year",
        lean_feed_scope="full",
        split_adjustment_mode="splits_backward",
        split_events_file=tmp_path / "missing.parquet",
        split_adjust_volume=True,
    )

    with pytest.raises(RuntimeError, match="split adjustment enabled"):
        convert_parquet_to_lean(cfg)


def test_convert_parquet_to_lean_rejects_1d_root(tmp_path: Path) -> None:
    parquet_root = tmp_path / "bars" / "1d"
    _write_symbol_year_file(
        parquet_root,
        symbol="TSLA",
        year=2024,
        rows=[
            {
                "symbol": "TSLA",
                "session_date": "2024-01-02",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000.0,
            }
        ],
    )

    cfg = ConversionConfig(
        parquet_root=parquet_root,
        lean_data_root=tmp_path / "lean_data",
        cache_root=tmp_path / "cache",
        tickers=["TSLA"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        timezone="America/New_York",
        rth_start="09:30",
        rth_end="16:00",
        source_layout="symbol_year",
        lean_feed_scope="full",
        split_adjustment_mode="none",
        split_events_file=tmp_path / "unused.parquet",
        split_adjust_volume=True,
    )

    with pytest.raises(ValueError, match="bars/1d"):
        convert_parquet_to_lean(cfg)
