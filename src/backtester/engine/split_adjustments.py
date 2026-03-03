"""Split-event parsing and backward-adjustment helpers for OHLCV bars."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PRICE_COLUMNS = ("open", "high", "low", "close")


class SplitAdjustmentError(RuntimeError):
    """Raised when split-event data is missing or malformed."""


@dataclass(frozen=True)
class SplitEvent:
    symbol: str
    ex_date: date
    ratio_new_over_old: float


def parse_split_ratio(raw: object) -> float:
    """Parse split ratio text like '5:1', '1/3', or scientific notation sides."""
    text = str(raw or "").strip()
    if not text:
        raise ValueError("split ratio is empty")

    lhs_text: str
    rhs_text: str
    if ":" in text:
        lhs_text, rhs_text = text.split(":", 1)
    elif "/" in text:
        lhs_text, rhs_text = text.split("/", 1)
    else:
        value = float(text)
        if value <= 0:
            raise ValueError("split ratio must be > 0")
        return value

    lhs = float(lhs_text.strip())
    rhs = float(rhs_text.strip())
    if lhs <= 0 or rhs <= 0:
        raise ValueError("split ratio sides must be > 0")
    return lhs / rhs


def load_split_events(
    split_events_file: Path,
    *,
    tickers: set[str] | None = None,
    end_date: date | None = None,
) -> dict[str, list[SplitEvent]]:
    """Load split events from CSV/parquet into per-symbol sorted events."""
    path = Path(split_events_file)
    if not path.exists():
        raise SplitAdjustmentError(
            f"split events file not found: {path}. Set data.split_events_file to a valid CSV/parquet path."
        )

    try:
        if path.suffix.lower() == ".parquet":
            raw = pd.read_parquet(path)
        elif path.suffix.lower() == ".csv":
            raw = pd.read_csv(path)
        else:
            raise SplitAdjustmentError(f"unsupported split events format: {path.suffix}")
    except SplitAdjustmentError:
        raise
    except Exception as exc:
        raise SplitAdjustmentError(f"failed to read split events file {path}: {exc}") from exc

    normalized = _normalize_split_frame(raw)
    if normalized.empty:
        return {}

    if tickers:
        ticker_filter = {t.upper() for t in tickers}
        normalized = normalized[normalized["symbol"].isin(ticker_filter)]
    if end_date is not None:
        normalized = normalized[normalized["ex_date"] <= end_date]
    if normalized.empty:
        return {}

    normalized = normalized.sort_values(["symbol", "ex_date", "ratio_new_over_old"])
    result: dict[str, list[SplitEvent]] = {}
    for symbol, group in normalized.groupby("symbol", sort=True):
        events = [
            SplitEvent(
                symbol=symbol,
                ex_date=row.ex_date,
                ratio_new_over_old=float(row.ratio_new_over_old),
            )
            for row in group.itertuples(index=False)
        ]
        if events:
            result[str(symbol)] = events
    return result


def apply_backward_split_adjustments(
    frame: pd.DataFrame,
    *,
    symbol_col: str,
    local_day: pd.Series,
    split_events: dict[str, list[SplitEvent]],
    adjust_volume: bool = True,
) -> tuple[pd.DataFrame, int, int]:
    """Apply backward split adjustment to OHLCV bars.

    For each row, cumulative split factor is the product of split ratios where
    split ex-date is strictly later than the row trading day.
    """
    if frame.empty or not split_events:
        return frame, 0, 0

    if symbol_col not in frame.columns:
        return frame, 0, 0
    if not set(PRICE_COLUMNS).issubset(frame.columns):
        return frame, 0, 0
    if "volume" not in frame.columns:
        return frame, 0, 0

    out = frame.copy()
    day_series = pd.to_datetime(local_day, errors="coerce").dt.date

    symbols_with_splits = 0
    rows_adjusted = 0
    eps = 1e-12

    for symbol_raw, idx in out.groupby(symbol_col).groups.items():
        symbol = str(symbol_raw or "").upper()
        events = split_events.get(symbol, [])
        if not events:
            continue

        index = pd.Index(idx)
        days = day_series.loc[index].to_numpy(dtype=object, copy=False)
        if len(days) == 0:
            continue

        factors = np.ones(len(days), dtype=float)
        for event in events:
            factors[days < event.ex_date] *= float(event.ratio_new_over_old)

        changed = np.abs(factors - 1.0) > eps
        if not changed.any():
            continue

        changed_index = index[changed]
        factors_changed = factors[changed]

        prices = out.loc[changed_index, list(PRICE_COLUMNS)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        prices = prices / factors_changed[:, None]
        out.loc[changed_index, list(PRICE_COLUMNS)] = prices

        if adjust_volume:
            volume = pd.to_numeric(out.loc[changed_index, "volume"], errors="coerce").to_numpy(dtype=float)
            out.loc[changed_index, "volume"] = volume * factors_changed

        symbols_with_splits += 1
        rows_adjusted += int(changed.sum())

    return out, symbols_with_splits, rows_adjusted


def split_events_digest(split_events: dict[str, list[SplitEvent]]) -> str:
    """Stable digest for all split events across symbols."""
    payload: list[dict[str, Any]] = []
    for symbol in sorted(split_events):
        payload.append(
            {
                "symbol": symbol,
                "events": [
                    {"ex_date": event.ex_date.isoformat(), "ratio": float(event.ratio_new_over_old)}
                    for event in split_events[symbol]
                ],
            }
        )
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def symbol_split_digest(events: list[SplitEvent]) -> str:
    raw = json.dumps(
        [{"ex_date": event.ex_date.isoformat(), "ratio": float(event.ratio_new_over_old)} for event in events],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_fingerprint(path: Path) -> str:
    stat = Path(path).stat()
    return f"{Path(path).resolve()}:{stat.st_size}:{stat.st_mtime_ns}"


def count_split_events(split_events: dict[str, list[SplitEvent]]) -> int:
    return sum(len(events) for events in split_events.values())


def _normalize_split_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "ex_date", "ratio_new_over_old"])

    lower_to_actual = {str(col).lower(): str(col) for col in frame.columns}
    symbol_col = lower_to_actual.get("symbol") or lower_to_actual.get("act_symbol") or lower_to_actual.get("ticker")
    ex_date_col = lower_to_actual.get("report_date") or lower_to_actual.get("ex_date") or lower_to_actual.get("date")
    ratio_col = lower_to_actual.get("split_factor")
    to_factor_col = lower_to_actual.get("to_factor")
    for_factor_col = lower_to_actual.get("for_factor")

    if not symbol_col or not ex_date_col:
        raise SplitAdjustmentError(
            "split events file must include symbol and ex-date columns (e.g. symbol/report_date or act_symbol/ex_date)"
        )
    if not ratio_col and not (to_factor_col and for_factor_col):
        raise SplitAdjustmentError(
            "split events file must include either split_factor or to_factor/for_factor columns"
        )

    out = pd.DataFrame()
    out["symbol"] = frame[symbol_col].astype(str).str.upper().str.strip()
    out["ex_date"] = pd.to_datetime(frame[ex_date_col], errors="coerce").dt.date

    if ratio_col:
        out["ratio_new_over_old"] = frame[ratio_col].map(parse_split_ratio)
    else:
        to_values = pd.to_numeric(frame[to_factor_col], errors="coerce")
        for_values = pd.to_numeric(frame[for_factor_col], errors="coerce")
        out["ratio_new_over_old"] = to_values / for_values

    out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=["symbol", "ex_date", "ratio_new_over_old"])
    out = out[out["symbol"] != ""]
    out = out[out["ratio_new_over_old"] > 0]
    return out.drop_duplicates(subset=["symbol", "ex_date", "ratio_new_over_old"], keep="last")
