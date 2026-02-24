"""Trade inspection panel with daily context and entry/exit execution charts."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from nicegui import ui

from backtester.gui.components.lightweight_chart import (
    LightweightCandleChart,
    bars_from_frame,
    markers_from_internal_markers,
)
from backtester.gui.services.config_service import DATASET_PARQUET_BASE, TIMEFRAME_ORDER
from backtester.gui.services.market_data_service import MarketDataService

DAILY_DEFAULT = "1d"
EXECUTION_DEFAULT = "5m"
INTRADAY_TIMEFRAMES = ("1m", "5m", "15m", "1h")
BAR_LIMIT = 20_000


def extract_trade_markers(trade: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Extract chart marker points for entry/exit from one trade."""
    warnings: list[str] = []
    markers: list[dict[str, Any]] = []

    entry_ts = _parse_trade_ts_ny(trade.get("entry_fill_ts_utc"))
    entry_price = _safe_float_or_none(trade.get("entry_price"))
    if entry_ts is None or entry_price is None:
        warnings.append("Entry marker omitted: invalid entry timestamp/price")
    else:
        markers.append(
            {
                "label": "Entry",
                "x": entry_ts,
                "y": entry_price,
                "color": "#16a34a",
                "symbol": "triangle-up",
            }
        )

    raw_exit_ts = trade.get("exit_fill_ts_utc")
    raw_exit_price = trade.get("exit_price")
    if raw_exit_ts not in (None, "") and raw_exit_price not in (None, ""):
        exit_ts = _parse_trade_ts_ny(raw_exit_ts)
        exit_price = _safe_float_or_none(raw_exit_price)
        if exit_ts is None or exit_price is None:
            warnings.append("Exit marker omitted: invalid exit timestamp/price")
        else:
            markers.append(
                {
                    "label": "Exit",
                    "x": exit_ts,
                    "y": exit_price,
                    "color": "#dc2626",
                    "symbol": "triangle-down",
                }
            )

    return markers, warnings


class TradeInspectionPanel:
    def __init__(self, market_data_service: MarketDataService | None = None) -> None:
        self.market_data = market_data_service or MarketDataService()

        self._run_config: dict[str, Any] = {}
        self._trades_by_id: dict[str, dict[str, Any]] = {}
        self._active_trade: dict[str, Any] | None = None

        self._source_roots: list[Path] = []
        self._catalog: dict[str, list[str]] = {}
        self._session: str | None = None
        self._daily_timeframe: str | None = None
        self._execution_timeframe: str | None = None

        self.root = ui.column().classes("w-full gap-3")
        with self.root:
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center gap-3"):
                    ui.label("Trade Inspection").classes("text-lg font-semibold")
                    self._trade_label = ui.label("Select a trade in the table to inspect.").classes("text-slate-500")

                with ui.grid(columns=4).classes("w-full gap-3 mt-2"):
                    self._source_select = ui.select(
                        label="Data Source",
                        options={},
                        on_change=lambda _: self._on_source_changed(),
                    )
                    self._session_select = ui.select(
                        label="Session",
                        options={},
                        on_change=lambda _: self._on_session_changed(),
                    )
                    self._daily_tf_select = ui.select(
                        label="Daily Timeframe",
                        options={},
                        on_change=lambda _: self._on_daily_timeframe_changed(),
                    )
                    self._execution_tf_select = ui.select(
                        label="Execution Timeframe",
                        options={},
                        on_change=lambda _: self._on_execution_timeframe_changed(),
                    )

                self._status_label = ui.label("").classes("text-sm text-slate-500")

            with ui.card().classes("w-full"):
                ui.label("Chart A: Daily Context").classes("text-sm uppercase tracking-wide text-slate-500")
                self._daily_chart = LightweightCandleChart(height=420)

            self._execution_grid = ui.element("div").classes("w-full grid grid-cols-1 xl:grid-cols-2 gap-3")
            with self._execution_grid:
                with ui.card().classes("w-full"):
                    ui.label("Chart B: Entry Execution").classes("text-sm uppercase tracking-wide text-slate-500")
                    self._entry_chart = LightweightCandleChart(height=360)
                with ui.card().classes("w-full"):
                    ui.label("Chart C: Exit Execution").classes("text-sm uppercase tracking-wide text-slate-500")
                    self._exit_chart = LightweightCandleChart(height=360)

    def set_context(self, run_config: dict[str, Any], trades: list[dict[str, Any]]) -> None:
        self._run_config = run_config or {}
        self._trades_by_id = {str(trade.get("trade_id") or idx): trade for idx, trade in enumerate(trades)}

        run_parquet_root = _run_parquet_root(self._run_config)
        self._source_roots = _discover_source_roots(run_parquet_root)
        self._refresh_source_options(run_parquet_root)

        if self._active_trade is not None:
            trade_id = str(self._active_trade.get("trade_id") or "")
            self._active_trade = self._trades_by_id.get(trade_id)

        if self._active_trade is None and trades:
            self._active_trade = trades[0]

        self._render_charts()

    def set_active_trade(self, trade: dict[str, Any] | None) -> None:
        self._active_trade = trade
        self._render_charts()

    def _refresh_source_options(self, run_parquet_root: Path | None) -> None:
        options = {str(root): str(root) for root in self._source_roots}
        self._source_select.options = options

        current = str(self._source_select.value or "").strip()
        preferred_base = _find_parquet_base(run_parquet_root) if run_parquet_root else None

        selected_base: Path | None = None
        if current and current in options:
            selected_base = Path(current)
        elif preferred_base is not None and str(preferred_base) in options:
            selected_base = preferred_base
        elif self._source_roots:
            selected_base = self._source_roots[0]

        if selected_base is None:
            self._source_select.value = None
            self._source_select.update()
            self._catalog = {}
            self._apply_catalog_defaults(None, None)
            return

        self._source_select.value = str(selected_base)
        self._source_select.update()
        self.market_data.set_parquet_base(selected_base)

        preferred_session = None
        preferred_timeframe = None
        if run_parquet_root is not None:
            preferred_session, preferred_timeframe = _session_timeframe_from_path(run_parquet_root)

        self._catalog = self.market_data.discover_catalog()
        self._apply_catalog_defaults(preferred_session, preferred_timeframe)

    def _apply_catalog_defaults(self, preferred_session: str | None, preferred_timeframe: str | None) -> None:
        sessions = sorted(self._catalog.keys())
        if not sessions:
            self._session = None
            self._daily_timeframe = None
            self._execution_timeframe = None
            self._session_select.options = {}
            self._daily_tf_select.options = {}
            self._execution_tf_select.options = {}
            self._session_select.value = None
            self._daily_tf_select.value = None
            self._execution_tf_select.value = None
            self._session_select.update()
            self._daily_tf_select.update()
            self._execution_tf_select.update()
            return

        previous_session = str(self._session_select.value or "")
        if previous_session in sessions:
            self._session = previous_session
        elif preferred_session in sessions:
            self._session = preferred_session
        elif "full" in sessions:
            self._session = "full"
        else:
            self._session = sessions[0]

        self._session_select.options = {item: item for item in sessions}
        self._session_select.value = self._session
        self._session_select.update()

        self._refresh_timeframe_options(preferred_timeframe)

    def _refresh_timeframe_options(self, preferred_timeframe: str | None = None) -> None:
        if not self._session:
            return

        all_timeframes = list(self._catalog.get(self._session, []))
        execution_timeframes = [tf for tf in all_timeframes if tf in INTRADAY_TIMEFRAMES]
        if not execution_timeframes:
            execution_timeframes = list(all_timeframes)

        self._daily_timeframe = _pick_daily_timeframe(
            all_timeframes,
            preferred=self._daily_timeframe or preferred_timeframe,
        )
        self._execution_timeframe = _pick_execution_timeframe(
            execution_timeframes,
            preferred=self._execution_timeframe or preferred_timeframe,
        )

        all_options = {tf: tf for tf in all_timeframes}
        self._daily_tf_select.options = all_options
        self._daily_tf_select.value = self._daily_timeframe
        self._daily_tf_select.update()

        exec_options = {tf: tf for tf in execution_timeframes}
        self._execution_tf_select.options = exec_options
        self._execution_tf_select.value = self._execution_timeframe
        self._execution_tf_select.update()

    def _on_source_changed(self) -> None:
        selected = str(self._source_select.value or "").strip()
        if not selected:
            return
        self.market_data.set_parquet_base(Path(selected))
        self._catalog = self.market_data.discover_catalog()
        self._apply_catalog_defaults(None, None)
        self._render_charts()

    def _on_session_changed(self) -> None:
        self._session = str(self._session_select.value or "").strip() or None
        self._refresh_timeframe_options()
        self._render_charts()

    def _on_daily_timeframe_changed(self) -> None:
        self._daily_timeframe = str(self._daily_tf_select.value or "").strip() or None
        self._render_charts()

    def _on_execution_timeframe_changed(self) -> None:
        self._execution_timeframe = str(self._execution_tf_select.value or "").strip() or None
        self._render_charts()

    def _render_charts(self) -> None:
        if self._active_trade is None:
            self._trade_label.text = "Select a trade in the table to inspect."
            self._status_label.text = ""
            self._daily_chart.clear("Select a trade to render daily context.")
            self._entry_chart.clear("Select a trade to render entry execution.")
            self._exit_chart.clear("Select a trade to render exit execution.")
            return

        if not self._session or not self._daily_timeframe or not self._execution_timeframe:
            self._status_label.text = "No prepared market data catalog available for trade inspection."
            self._daily_chart.clear("No session/timeframe data found.")
            self._entry_chart.clear("No session/timeframe data found.")
            self._exit_chart.clear("No session/timeframe data found.")
            return

        symbol = str(self._active_trade.get("symbol") or "").upper().strip()
        trade_id = str(self._active_trade.get("trade_id") or "-")
        self._trade_label.text = f"{trade_id} · {symbol}"

        windows = self.market_data.make_trade_windows(
            self._active_trade.get("entry_fill_ts_utc"),
            self._active_trade.get("exit_fill_ts_utc"),
        )
        markers, warnings = extract_trade_markers(self._active_trade)
        entry_marker = _first_marker(markers, "Entry")
        exit_marker = _first_marker(markers, "Exit")

        daily_title = f"{symbol} · {self._session}/{self._daily_timeframe}"
        daily_bars = self.market_data.load_bars(
            symbol,
            self._session,
            self._daily_timeframe,
            windows["daily"][0],
            windows["daily"][1],
        )

        entry_title = f"{symbol} · Entry · {self._session}/{self._execution_timeframe}"
        entry_bars = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
        if entry_marker is not None:
            entry_start, entry_end = _focus_window(entry_marker["x"], self._execution_timeframe)
            entry_bars = self.market_data.load_bars(
                symbol,
                self._session,
                self._execution_timeframe,
                entry_start,
                entry_end,
            )

        exit_title = f"{symbol} · Exit · {self._session}/{self._execution_timeframe}"
        exit_bars = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
        if exit_marker is not None:
            exit_start, exit_end = _focus_window(exit_marker["x"], self._execution_timeframe)
            exit_bars = self.market_data.load_bars(
                symbol,
                self._session,
                self._execution_timeframe,
                exit_start,
                exit_end,
            )

        daily_bars, daily_capped = _cap_bars(daily_bars, BAR_LIMIT)
        entry_bars, entry_capped = _cap_bars(entry_bars, BAR_LIMIT)
        exit_bars, exit_capped = _cap_bars(exit_bars, BAR_LIMIT)
        if daily_capped:
            warnings.append(f"Daily chart capped to {BAR_LIMIT:,} bars")
        if entry_capped:
            warnings.append(f"Entry chart capped to {BAR_LIMIT:,} bars")
        if exit_capped:
            warnings.append(f"Exit chart capped to {BAR_LIMIT:,} bars")

        warnings.extend(_marker_window_warnings(daily_bars, markers, daily_title))
        warnings.extend(_marker_window_warnings(entry_bars, [entry_marker] if entry_marker else [], entry_title))
        warnings.extend(_marker_window_warnings(exit_bars, [exit_marker] if exit_marker else [], exit_title))

        daily_payload_bars = bars_from_frame(daily_bars)
        daily_payload_markers = markers_from_internal_markers(
            markers,
            bars=daily_payload_bars,
            timeframe=self._daily_timeframe,
        )
        self._daily_chart.render(
            bars=daily_payload_bars,
            markers=daily_payload_markers,
            title=daily_title,
            empty_message=f"No bars found for {daily_title}",
        )

        entry_payload_bars = bars_from_frame(entry_bars)
        entry_payload_markers = markers_from_internal_markers(
            [entry_marker] if entry_marker else [],
            bars=entry_payload_bars,
            timeframe=self._execution_timeframe,
        )
        self._entry_chart.render(
            bars=entry_payload_bars,
            markers=entry_payload_markers,
            title=entry_title,
            empty_message=f"No bars found for {entry_title}",
        )

        if exit_marker is None:
            self._exit_chart.clear("This trade has no exit fill yet.")
        else:
            exit_payload_bars = bars_from_frame(exit_bars)
            exit_payload_markers = markers_from_internal_markers(
                [exit_marker],
                bars=exit_payload_bars,
                timeframe=self._execution_timeframe,
            )
            self._exit_chart.render(
                bars=exit_payload_bars,
                markers=exit_payload_markers,
                title=exit_title,
                empty_message=f"No bars found for {exit_title}",
            )

        self._status_label.text = " | ".join(dict.fromkeys(warnings))


def _marker_window_warnings(
    bars: pd.DataFrame,
    markers: list[dict[str, Any] | None],
    title: str,
) -> list[str]:
    warnings: list[str] = []
    valid_markers = [marker for marker in markers if marker is not None]
    if bars.empty:
        warnings.append(f"No bars found for {title}")
        return warnings

    min_ts = bars["ts"].min()
    max_ts = bars["ts"].max()
    for marker in valid_markers:
        marker_ts = marker.get("x")
        label = str(marker.get("label") or "Marker")
        if marker_ts is None:
            continue
        if not (min_ts <= marker_ts <= max_ts):
            warnings.append(f"{label} marker outside chart window ({title})")
    return warnings


def _cap_bars(frame: pd.DataFrame, limit: int) -> tuple[pd.DataFrame, bool]:
    if len(frame) <= limit:
        return frame, False
    return frame.tail(limit).reset_index(drop=True), True


def _focus_window(anchor: Any, timeframe: str) -> tuple[datetime, datetime]:
    anchor_ts = _parse_trade_ts_ny(anchor)
    if anchor_ts is None:
        now = pd.Timestamp.now(tz="America/New_York")
        return (
            (now - pd.Timedelta(days=1)).to_pydatetime(),
            (now + pd.Timedelta(days=1)).to_pydatetime(),
        )

    anchor_day = anchor_ts.floor("D")
    if timeframe == "1h":
        before = pd.Timedelta(days=3)
        after = pd.Timedelta(days=3)
    else:
        before = pd.Timedelta(days=1)
        after = pd.Timedelta(days=1)

    return (
        (anchor_day - before).to_pydatetime(),
        (anchor_day + after).to_pydatetime(),
    )


def _parse_trade_ts_ny(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    try:
        ts = pd.to_datetime(value, errors="coerce", utc=True)
    except Exception:
        return None
    if pd.isna(ts) or not isinstance(ts, pd.Timestamp):
        return None
    return ts.tz_convert("America/New_York")


def _safe_float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _first_marker(markers: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    for marker in markers:
        if marker.get("label") == label:
            return marker
    return None


def _run_parquet_root(run_config: dict[str, Any]) -> Path | None:
    try:
        root = (run_config.get("data") or {}).get("parquet_root")
        if not root:
            return None
        return Path(str(root))
    except Exception:
        return None


def _discover_source_roots(run_parquet_root: Path | None) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    def add(path: Path | None) -> None:
        if path is None:
            return
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        key = str(resolved)
        if key in seen:
            return
        if resolved.exists() and resolved.is_dir():
            seen.add(key)
            roots.append(resolved)

    add(DATASET_PARQUET_BASE)
    add(_find_parquet_base(run_parquet_root) if run_parquet_root else None)

    return sorted(roots, key=lambda p: str(p))


def _find_parquet_base(path: Path | None) -> Path | None:
    if path is None:
        return None

    current = Path(path)
    if current.name.startswith("timeframe=") and current.parent.name.startswith("session="):
        current = current.parent.parent

    for candidate in [current, *current.parents]:
        if candidate.name == "parquet":
            return candidate
    return None


def _session_timeframe_from_path(path: Path) -> tuple[str | None, str | None]:
    session = None
    timeframe = None
    for part in path.parts:
        if part.startswith("session="):
            session = part.split("=", 1)[1]
        elif part.startswith("timeframe="):
            timeframe = part.split("=", 1)[1]
    return session, timeframe


def _pick_daily_timeframe(timeframes: list[str], preferred: str | None = None) -> str:
    if not timeframes:
        return DAILY_DEFAULT
    if preferred and preferred in timeframes:
        return preferred
    if DAILY_DEFAULT in timeframes:
        return DAILY_DEFAULT
    return sorted(timeframes, key=lambda tf: TIMEFRAME_ORDER.get(tf, 99))[-1]


def _pick_execution_timeframe(timeframes: list[str], preferred: str | None = None) -> str:
    if not timeframes:
        return EXECUTION_DEFAULT
    if preferred and preferred in timeframes:
        return preferred
    for candidate in (EXECUTION_DEFAULT, "1m", "15m", "1h"):
        if candidate in timeframes:
            return candidate
    return sorted(timeframes, key=lambda tf: TIMEFRAME_ORDER.get(tf, 99))[0]
