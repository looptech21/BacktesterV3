"""Parquet to LEAN format data converter.

Simplified from BacktesterV2/data_tools/lean_converter.py.
Converts monthly parquet OHLCV files into LEAN equity minute zipped CSVs.
Supports incremental conversion via file hash caching.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class ConversionConfig:
    parquet_root: Path
    lean_data_root: Path
    cache_root: Path
    tickers: list[str]
    start_date: date
    end_date: date
    timezone: str
    rth_start: str
    rth_end: str


@dataclass(frozen=True)
class ConversionReport:
    converted_files: int
    skipped_files: int
    rows_written: int


def _month_files(parquet_root: Path, start: date, end: date) -> list[Path]:
    files = []
    for path in sorted(parquet_root.glob("ohlcv_*.parquet")):
        stem = path.stem.replace("ohlcv_", "")
        try:
            year_text, month_text = stem.split("-")
            month_start = date(int(year_text), int(month_text), 1)
        except Exception:
            continue
        month_end = (month_start + timedelta(days=31)).replace(day=1) - timedelta(days=1)
        if month_end < start or month_start > end:
            continue
        files.append(path)
    return files


def _lean_zip_path(lean_root: Path, symbol: str, trading_day: date) -> Path:
    return (
        lean_root / "equity" / "usa" / "minute" / symbol.lower() / f"{trading_day.strftime('%Y%m%d')}_trade.zip"
    )


def _lean_member_name(symbol: str, trading_day: date) -> str:
    return f"{trading_day.strftime('%Y%m%d')}_{symbol.lower()}_minute_trade.csv"


def _to_lean_csv_bytes(df: pd.DataFrame) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    for row in df.itertuples(index=False):
        ts_est = row.timestamp_est
        ms_since_midnight = (
            ((int(ts_est.hour) * 60 + int(ts_est.minute)) * 60 + int(ts_est.second)) * 1000
            + int(ts_est.microsecond) // 1000
        )
        writer.writerow([
            ms_since_midnight,
            int(round(float(row.open) * 10000.0)),
            int(round(float(row.high) * 10000.0)),
            int(round(float(row.low) * 10000.0)),
            int(round(float(row.close) * 10000.0)),
            int(round(float(row.volume))),
        ])
    return out.getvalue().encode("utf-8")


def convert_parquet_to_lean(
    conv_config: ConversionConfig,
    *,
    progress_callback: object | None = None,
) -> ConversionReport:
    """Convert parquet OHLCV files to LEAN minute data format."""
    files = _month_files(conv_config.parquet_root, conv_config.start_date, conv_config.end_date)
    if not files:
        return ConversionReport(converted_files=0, skipped_files=0, rows_written=0)

    lean_root = conv_config.lean_data_root
    lean_root.mkdir(parents=True, exist_ok=True)
    tickers_upper = {t.upper() for t in conv_config.tickers}

    total_converted = 0
    total_skipped = 0
    total_rows = 0

    rth_start_parts = conv_config.rth_start.split(":")
    rth_end_parts = conv_config.rth_end.split(":")
    rth_start_minute = int(rth_start_parts[0]) * 60 + int(rth_start_parts[1])
    rth_end_minute = int(rth_end_parts[0]) * 60 + int(rth_end_parts[1])

    for file_path in files:
        try:
            df = pd.read_parquet(file_path)
        except Exception:
            continue

        if "ticker" not in df.columns or "timestamp" not in df.columns:
            continue

        df = df[df["ticker"].str.upper().isin(tickers_upper)].copy()
        if df.empty:
            continue

        # Apply RTH session filter
        ts = pd.to_datetime(df["timestamp"], utc=True)
        ts_local = ts.dt.tz_convert(conv_config.timezone)
        df["timestamp_est"] = ts_local
        minute_of_day = ts_local.dt.hour * 60 + ts_local.dt.minute
        df = df[(minute_of_day >= rth_start_minute) & (minute_of_day < rth_end_minute)].copy()
        if df.empty:
            continue

        df["trading_day"] = ts_local.dt.date

        for (ticker, day), group in df.groupby(["ticker", "trading_day"]):
            symbol = str(ticker).upper()
            trading_day = day if isinstance(day, date) else day

            zip_path = _lean_zip_path(lean_root, symbol, trading_day)
            if zip_path.exists():
                total_skipped += 1
                continue

            zip_path.parent.mkdir(parents=True, exist_ok=True)
            sorted_group = group.sort_values("timestamp_est")
            csv_bytes = _to_lean_csv_bytes(sorted_group)

            member_name = _lean_member_name(symbol, trading_day)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(member_name, csv_bytes)

            total_converted += 1
            total_rows += len(sorted_group)

    return ConversionReport(
        converted_files=total_converted,
        skipped_files=total_skipped,
        rows_written=total_rows,
    )
