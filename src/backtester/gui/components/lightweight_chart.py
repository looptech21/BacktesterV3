"""NiceGUI wrapper for TradingView Lightweight Charts candlestick rendering."""

from __future__ import annotations

import json
from bisect import bisect_left
from typing import Any

import pandas as pd
from nicegui import ui


def bars_from_frame(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert normalized bar frame to Lightweight Charts payload bars."""
    if frame.empty:
        return []

    required = {"ts", "open", "high", "low", "close"}
    if not required.issubset(set(frame.columns)):
        return []

    work = frame.loc[:, ["ts", "open", "high", "low", "close"]].copy()
    ts = pd.to_datetime(work["ts"], errors="coerce")
    if getattr(ts.dt, "tz", None) is None:
        ts = ts.dt.tz_localize("UTC", ambiguous="NaT", nonexistent="NaT")
    else:
        ts = ts.dt.tz_convert("UTC")

    work["time"] = [
        int(value.timestamp()) if isinstance(value, pd.Timestamp) and not pd.isna(value) else None
        for value in ts
    ]
    for col in ("open", "high", "low", "close"):
        work[col] = pd.to_numeric(work[col], errors="coerce")

    work = work.dropna(subset=["time", "open", "high", "low", "close"])
    if work.empty:
        return []

    work = work.sort_values("time")
    work = work.drop_duplicates(subset=["time"], keep="last")

    return [
        {
            "time": int(row["time"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }
        for row in work.to_dict(orient="records")
    ]


def markers_from_internal_markers(
    markers: list[dict[str, Any]],
    *,
    bars: list[dict[str, Any]] | None = None,
    timeframe: str | None = None,
) -> list[dict[str, Any]]:
    """Convert internal marker representation to Lightweight Charts marker payload."""
    converted: list[dict[str, Any]] = []
    for marker in markers:
        x_raw = marker.get("x")
        y_raw = marker.get("y")
        if x_raw is None or y_raw is None:
            continue

        ts = pd.to_datetime(x_raw, errors="coerce")
        if pd.isna(ts):
            continue
        if not isinstance(ts, pd.Timestamp):
            continue
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

        try:
            price = float(y_raw)
        except Exception:
            continue

        shape = "arrowUp"
        symbol = str(marker.get("symbol") or "")
        label = str(marker.get("label") or "")
        if symbol == "triangle-down" or label.lower() == "exit":
            shape = "arrowDown"

        converted.append(
            {
                "time": int(ts.timestamp()),
                "price": price,
                "position": {"type": "price", "price": price},
                "shape": shape,
                "color": str(marker.get("color") or "#22c55e"),
                "text": label,
            }
        )

    converted.sort(key=lambda item: (item["time"], item["price"]))
    if not converted:
        return converted

    if not bars:
        return converted

    return _attach_logical_positions(converted, bars, timeframe)


def _attach_logical_positions(
    markers: list[dict[str, Any]],
    bars: list[dict[str, Any]],
    timeframe: str | None,
) -> list[dict[str, Any]]:
    bar_times = _bar_times(bars)
    if not bar_times:
        return markers

    if timeframe == "1d":
        return _attach_daily_logical_positions(markers, bar_times)
    return _attach_intraday_logical_positions(markers, bar_times)


def _bar_times(bars: list[dict[str, Any]]) -> list[int]:
    times: list[int] = []
    for bar in bars:
        raw = bar.get("time") if isinstance(bar, dict) else None
        try:
            value = int(raw)
        except Exception:
            continue
        times.append(value)
    if not times:
        return []
    return sorted(set(times))


def _attach_intraday_logical_positions(markers: list[dict[str, Any]], bar_times: list[int]) -> list[dict[str, Any]]:
    if len(bar_times) < 2:
        first = float(bar_times[0])
        for marker in markers:
            if int(marker["time"]) == int(first):
                marker["logical"] = 0.0
        return markers

    for marker in markers:
        logical = _interpolated_logical_index(int(marker["time"]), bar_times)
        if logical is not None:
            marker["logical"] = logical

    return markers


def _attach_daily_logical_positions(markers: list[dict[str, Any]], bar_times: list[int]) -> list[dict[str, Any]]:
    bar_dates = [
        pd.Timestamp.fromtimestamp(ts, tz="UTC").tz_convert("America/New_York").date()
        for ts in bar_times
    ]
    date_to_index = {bar_date: idx for idx, bar_date in enumerate(bar_dates)}
    if not date_to_index:
        return markers

    for marker in markers:
        marker_date = pd.Timestamp.fromtimestamp(int(marker["time"]), tz="UTC").tz_convert("America/New_York").date()
        nearest_index = date_to_index.get(marker_date)
        if nearest_index is None:
            nearest_index = min(
                range(len(bar_dates)),
                key=lambda idx: abs((bar_dates[idx] - marker_date).days),
            )
        marker["logical"] = float(nearest_index)

    return markers


def _interpolated_logical_index(marker_time: int, bar_times: list[int]) -> float | None:
    pos = bisect_left(bar_times, marker_time)
    if pos < len(bar_times) and bar_times[pos] == marker_time:
        return float(pos)
    if pos == 0 or pos >= len(bar_times):
        return None

    left_idx = pos - 1
    right_idx = pos
    left_time = bar_times[left_idx]
    right_time = bar_times[right_idx]
    if right_time <= left_time:
        return float(left_idx)

    span = float(right_time - left_time)
    offset = float(marker_time - left_time)
    return float(left_idx) + (offset / span)


class LightweightCandleChart:
    """Thin Python wrapper that pushes chart payloads to the browser bridge."""

    def __init__(self, *, height: int = 400, container_id: int | None = None) -> None:
        self.height = int(height)
        self.container = None
        if container_id is None:
            self.container = (
                ui.element("div")
                .classes("w-full")
                .style(f"height: {self.height}px; min-height: {self.height}px; position: relative;")
            )
            self.container_id = int(self.container.id)
        else:
            self.container_id = int(container_id)

    def render(
        self,
        *,
        bars: list[dict[str, Any]],
        markers: list[dict[str, Any]],
        title: str,
        empty_message: str | None = None,
    ) -> None:
        if not bars:
            self.clear(empty_message or "No data")
            return

        payload = {
            "containerId": self.container_id,
            "height": self.height,
            "title": title,
            "bars": bars,
            "markers": markers,
            "emptyMessage": empty_message or "",
        }
        self._bridge_call("render", payload)

    def clear(self, message: str) -> None:
        payload = {
            "containerId": self.container_id,
            "message": message,
        }
        self._bridge_call("clear", payload)

    def _bridge_call(self, method: str, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        code = (
            f"if (window.BacktesterLWC && typeof window.BacktesterLWC.{method} === 'function') "
            f"window.BacktesterLWC.{method}({encoded});"
        )
        ui.run_javascript(code)
