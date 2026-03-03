"""Parquet to LEAN format data converter.

Converts OHLCV parquet data into LEAN equity minute zipped CSVs.
Supports incremental conversion via signature sidecars.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from backtester.engine.split_adjustments import (
    SplitAdjustmentError,
    apply_backward_split_adjustments,
    count_split_events,
    file_fingerprint,
    load_split_events,
    split_events_digest,
    symbol_split_digest,
)

CONVERTER_SIGNATURE_VERSION = "split-aware-v3"
SourceLayout = Literal["symbol_year"]
LeanFeedScope = Literal["full", "rth"]


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
    source_layout: SourceLayout = "symbol_year"
    lean_feed_scope: LeanFeedScope = "full"
    split_adjustment_mode: str = "none"
    split_events_file: Path = Path(
        "/mnt/Daten/Backtest_data/Stock_splits_corporate_actions/hf_defeatbeta_stock_split_events_2026-02-16.parquet"
    )
    split_adjust_volume: bool = True


@dataclass(frozen=True)
class ConversionReport:
    converted_files: int
    skipped_files: int
    rows_written: int
    split_events_loaded: int = 0
    symbols_with_splits: int = 0
    rows_adjusted: int = 0


def _symbol_year_files(parquet_root: Path, tickers: set[str], start: date, end: date) -> list[Path]:
    files: list[Path] = []
    years = set(range(start.year, end.year + 1))

    if tickers:
        for symbol in sorted(tickers):
            symbol_dir = parquet_root / f"symbol={symbol}"
            if not symbol_dir.exists():
                continue
            for path in sorted(symbol_dir.glob("year=*.parquet")):
                try:
                    year = int(path.stem.split("=", 1)[1])
                except Exception:
                    continue
                if year in years:
                    files.append(path)
    else:
        for path in sorted(parquet_root.glob("symbol=*/year=*.parquet")):
            try:
                year = int(path.stem.split("=", 1)[1])
            except Exception:
                continue
            if year in years:
                files.append(path)

    return files


def _normalize_source_frame(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    rename_map: dict[str, str] = {}
    if "ticker" not in frame.columns and "symbol" in frame.columns:
        rename_map["symbol"] = "ticker"
    if "timestamp" not in frame.columns and "ts_utc" in frame.columns:
        rename_map["ts_utc"] = "timestamp"
    if rename_map:
        frame = frame.rename(columns=rename_map)

    required = {"timestamp", "ticker", "open", "high", "low", "close", "volume"}
    if not required.issubset(set(frame.columns)):
        return pd.DataFrame()

    keep_cols = ["timestamp", "ticker", "open", "high", "low", "close", "volume"]
    if "is_rth" in frame.columns:
        keep_cols.append("is_rth")
    return frame[keep_cols].copy()


def _lean_zip_path(lean_root: Path, symbol: str, trading_day: date) -> Path:
    return (
        lean_root / "equity" / "usa" / "minute" / symbol.lower() / f"{trading_day.strftime('%Y%m%d')}_trade.zip"
    )


def _zip_meta_path(zip_path: Path) -> Path:
    return zip_path.with_name(f"{zip_path.name}.meta.json")


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


def _source_file_signature(source_file: Path) -> dict[str, Any]:
    stat = source_file.stat()
    return {
        "source_path": str(source_file.resolve()),
        "source_size": int(stat.st_size),
        "source_mtime_ns": int(stat.st_mtime_ns),
    }


def _build_group_signature(
    *,
    source_signature: dict[str, Any],
    source_layout: SourceLayout,
    lean_feed_scope: LeanFeedScope,
    symbol: str,
    trading_day: date,
    timezone: str,
    rth_start: str,
    rth_end: str,
    split_mode: str,
    split_adjust_volume: bool,
    split_source_fingerprint: str,
    split_events_digest_value: str,
    symbol_split_digest_value: str,
) -> dict[str, Any]:
    return {
        "version": CONVERTER_SIGNATURE_VERSION,
        "source_layout": source_layout,
        "lean_feed_scope": lean_feed_scope,
        "symbol": symbol,
        "trading_day": trading_day.isoformat(),
        "timezone": timezone,
        "rth_start": rth_start,
        "rth_end": rth_end,
        "split_mode": split_mode,
        "split_adjust_volume": bool(split_adjust_volume),
        "split_source_fingerprint": split_source_fingerprint,
        "split_events_digest": split_events_digest_value,
        "symbol_split_digest": symbol_split_digest_value,
        **source_signature,
    }


def _meta_matches(meta_path: Path, expected_signature: dict[str, Any]) -> bool:
    if not meta_path.exists():
        return False
    try:
        existing = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return existing == expected_signature


def _write_meta(meta_path: Path, signature: dict[str, Any]) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(signature, indent=2, sort_keys=True), encoding="utf-8")


def _looks_like_daily_root(parquet_root: Path) -> bool:
    if parquet_root.name == "1d":
        return True
    parts = [p.lower() for p in parquet_root.parts]
    return len(parts) >= 2 and parts[-2:] == ["bars", "1d"]


def _validate_conversion_input(conv_config: ConversionConfig, files: list[Path]) -> None:
    if conv_config.source_layout != "symbol_year":
        raise ValueError(f"unsupported source_layout: {conv_config.source_layout}")

    if _looks_like_daily_root(conv_config.parquet_root):
        raise ValueError(
            "bars/1d is not supported for LEAN minute conversion. "
            "Use an intraday root such as bars/5m, bars/15m, or bars/1h."
        )

    if not files:
        raise ValueError(
            "no symbol/year parquet files found under data.parquet_root for the requested period/tickers"
        )


def convert_parquet_to_lean(
    conv_config: ConversionConfig,
    *,
    progress_callback: object | None = None,
) -> ConversionReport:
    """Convert parquet OHLCV files to LEAN minute data format."""
    del progress_callback

    tickers_upper = {t.upper() for t in conv_config.tickers}
    files = _symbol_year_files(
        conv_config.parquet_root,
        tickers_upper,
        conv_config.start_date,
        conv_config.end_date,
    )
    _validate_conversion_input(conv_config, files)

    lean_root = conv_config.lean_data_root
    lean_root.mkdir(parents=True, exist_ok=True)

    split_mode = str(conv_config.split_adjustment_mode or "none")
    if split_mode not in {"none", "splits_backward"}:
        raise ValueError(f"unsupported split_adjustment_mode: {split_mode}")

    if conv_config.lean_feed_scope not in {"full", "rth"}:
        raise ValueError(f"unsupported lean_feed_scope: {conv_config.lean_feed_scope}")

    split_events: dict[str, list[Any]] = {}
    split_source_fp = ""
    split_digest = ""
    if split_mode == "splits_backward":
        try:
            split_events = load_split_events(
                Path(conv_config.split_events_file),
                tickers=tickers_upper,
                end_date=conv_config.end_date,
            )
        except SplitAdjustmentError as exc:
            raise RuntimeError(
                f"split adjustment enabled but split events could not be loaded: {exc}"
            ) from exc
        split_source_fp = file_fingerprint(Path(conv_config.split_events_file))
        split_digest = split_events_digest(split_events)

    total_converted = 0
    total_skipped = 0
    total_rows = 0
    total_rows_adjusted = 0
    symbols_with_splits_seen: set[str] = set()
    split_events_loaded = count_split_events(split_events)

    rth_start_parts = conv_config.rth_start.split(":")
    rth_end_parts = conv_config.rth_end.split(":")
    rth_start_minute = int(rth_start_parts[0]) * 60 + int(rth_start_parts[1])
    rth_end_minute = int(rth_end_parts[0]) * 60 + int(rth_end_parts[1])

    for file_path in files:
        try:
            raw = pd.read_parquet(file_path)
        except Exception:
            continue

        df = _normalize_source_frame(raw)
        if df.empty:
            continue

        df["ticker"] = df["ticker"].astype(str).str.upper()
        df = df[df["ticker"].isin(tickers_upper)].copy()
        if df.empty:
            continue

        ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        valid_ts = ts.notna()
        if not valid_ts.any():
            continue

        df = df.loc[valid_ts].copy()
        ts = ts.loc[valid_ts]
        ts_local = ts.dt.tz_convert(conv_config.timezone)

        local_day = ts_local.dt.date
        in_range = (local_day >= conv_config.start_date) & (local_day <= conv_config.end_date)
        if not in_range.any():
            continue

        df = df.loc[in_range].copy()
        ts_local = ts_local.loc[in_range]
        df["timestamp_est"] = ts_local

        if conv_config.lean_feed_scope == "rth":
            if "is_rth" in df.columns:
                is_rth = df["is_rth"].fillna(False).astype(bool)
                df = df.loc[is_rth].copy()
            else:
                minute_of_day = ts_local.dt.hour * 60 + ts_local.dt.minute
                df = df[(minute_of_day >= rth_start_minute) & (minute_of_day < rth_end_minute)].copy()
            if df.empty:
                continue

        df["trading_day"] = df["timestamp_est"].dt.date

        if split_mode == "splits_backward" and split_events:
            df, _, rows_adjusted = apply_backward_split_adjustments(
                df,
                symbol_col="ticker",
                local_day=df["trading_day"],
                split_events=split_events,
                adjust_volume=bool(conv_config.split_adjust_volume),
            )
            total_rows_adjusted += rows_adjusted
            symbols_in_file = set(df["ticker"].astype(str).str.upper().unique())
            symbols_with_splits_seen.update(symbols_in_file.intersection(split_events.keys()))

        source_signature = _source_file_signature(file_path)

        for (ticker, day), group in df.groupby(["ticker", "trading_day"]):
            symbol = str(ticker).upper()
            trading_day = day if isinstance(day, date) else day

            zip_path = _lean_zip_path(lean_root, symbol, trading_day)
            meta_path = _zip_meta_path(zip_path)
            symbol_digest = symbol_split_digest(split_events.get(symbol, [])) if split_mode == "splits_backward" else ""
            signature = _build_group_signature(
                source_signature=source_signature,
                source_layout=conv_config.source_layout,
                lean_feed_scope=conv_config.lean_feed_scope,
                symbol=symbol,
                trading_day=trading_day,
                timezone=conv_config.timezone,
                rth_start=conv_config.rth_start,
                rth_end=conv_config.rth_end,
                split_mode=split_mode,
                split_adjust_volume=bool(conv_config.split_adjust_volume),
                split_source_fingerprint=split_source_fp,
                split_events_digest_value=split_digest,
                symbol_split_digest_value=symbol_digest,
            )

            if zip_path.exists() and _meta_matches(meta_path, signature):
                total_skipped += 1
                continue

            zip_path.parent.mkdir(parents=True, exist_ok=True)
            sorted_group = group.sort_values("timestamp_est")
            csv_bytes = _to_lean_csv_bytes(sorted_group)

            member_name = _lean_member_name(symbol, trading_day)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(member_name, csv_bytes)
            _write_meta(meta_path, signature)

            total_converted += 1
            total_rows += len(sorted_group)

    return ConversionReport(
        converted_files=total_converted,
        skipped_files=total_skipped,
        rows_written=total_rows,
        split_events_loaded=split_events_loaded,
        symbols_with_splits=len(symbols_with_splits_seen),
        rows_adjusted=total_rows_adjusted,
    )
