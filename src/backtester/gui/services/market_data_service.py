"""GUI service: discover and load preprocessed market bars for trade inspection."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from backtester.engine.split_adjustments import apply_backward_split_adjustments, load_split_events
from backtester.gui.services.config_service import DATASET_PARQUET_BASE, TIMEFRAME_ORDER

NY_TZ = "America/New_York"
BAR_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]
INTRADAY_TIMEFRAMES = {"1m", "5m", "15m", "1h"}
SESSIONS = ("full", "rth", "premarket", "postmarket")
RTH_START_MINUTE = 9 * 60 + 30
RTH_END_MINUTE = 16 * 60


class MarketDataService:
    """Read-only access to prepared parquet bars for chart inspection."""

    def __init__(self, parquet_base: Path | None = None, *, max_cache_files: int = 64) -> None:
        self.parquet_base = Path(parquet_base or DATASET_PARQUET_BASE)
        self.max_cache_files = max(8, int(max_cache_files))
        self._catalog_cache: dict[str, list[str]] | None = None
        self._file_cache: dict[str, pd.DataFrame] = {}

        self._split_mode = "none"
        self._split_events_file: Path | None = None
        self._split_adjust_volume = True
        self._split_end_date: date | None = None
        self._split_timezone = NY_TZ
        self._split_cache_key: tuple[str, str, str, bool, str] | None = None
        self._split_events_cache: dict[str, list] | None = None
        self._runtime_warnings: list[str] = []

    def set_parquet_base(self, parquet_base: Path) -> None:
        target = Path(parquet_base)
        if target == self.parquet_base:
            return
        self.parquet_base = target
        self._catalog_cache = None

    def configure_split_adjustment(
        self,
        *,
        mode: str,
        split_events_file: Path | None,
        adjust_volume: bool,
        end_date: date | None,
        timezone: str = NY_TZ,
    ) -> None:
        normalized_mode = str(mode or "none")
        if normalized_mode not in {"none", "splits_backward"}:
            normalized_mode = "none"

        normalized_file = Path(split_events_file) if split_events_file else None
        normalized_timezone = str(timezone or NY_TZ)

        if (
            normalized_mode == self._split_mode
            and normalized_file == self._split_events_file
            and bool(adjust_volume) == self._split_adjust_volume
            and end_date == self._split_end_date
            and normalized_timezone == self._split_timezone
        ):
            return

        self._split_mode = normalized_mode
        self._split_events_file = normalized_file
        self._split_adjust_volume = bool(adjust_volume)
        self._split_end_date = end_date
        self._split_timezone = normalized_timezone
        self._split_cache_key = None
        self._split_events_cache = None

        if normalized_mode == "splits_backward":
            self._warn(
                "Split adjustment is enabled for chart loading; frozen dataset is already split-adjusted and may be double-adjusted."
            )

    def clear_runtime_warnings(self) -> None:
        self._runtime_warnings = []

    def pop_runtime_warnings(self) -> list[str]:
        warnings = list(self._runtime_warnings)
        self._runtime_warnings = []
        return warnings

    def discover_catalog(self) -> dict[str, list[str]]:
        if self._catalog_cache is not None:
            return {session: list(timeframes) for session, timeframes in self._catalog_cache.items()}

        timeframes: list[str] = []
        if self.parquet_base.exists():
            for timeframe_dir in sorted(self.parquet_base.iterdir()):
                if not timeframe_dir.is_dir():
                    continue
                timeframe = timeframe_dir.name
                if timeframe not in TIMEFRAME_ORDER:
                    continue
                if list(timeframe_dir.glob("symbol=*/year=*.parquet")):
                    timeframes.append(timeframe)

        ordered = sorted(set(timeframes), key=lambda tf: TIMEFRAME_ORDER.get(tf, 99))
        catalog = {session: list(ordered) for session in SESSIONS} if ordered else {}

        self._catalog_cache = catalog
        return {session: list(timeframes) for session, timeframes in catalog.items()}

    def resolve_parquet_root(self, session: str, timeframe: str) -> Path | None:
        del session
        root = self.parquet_base / str(timeframe)
        if root.exists() and root.is_dir():
            return root
        return None

    def load_bars(
        self,
        symbol: str,
        session: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        root = self.resolve_parquet_root(session, timeframe)
        if root is None:
            return _empty_bar_frame()

        start_ts = _to_ny_timestamp(start)
        end_ts = _to_ny_timestamp(end)
        if start_ts is None or end_ts is None or end_ts < start_ts:
            return _empty_bar_frame()

        symbol_upper = str(symbol or "").upper().strip()
        if not symbol_upper:
            return _empty_bar_frame()

        bars: list[pd.DataFrame] = []
        for year in _iter_years(start_ts, end_ts):
            year_file = root / f"symbol={symbol_upper}" / f"year={year}.parquet"
            if not year_file.exists():
                continue

            raw = self._read_file(year_file)
            if raw.empty:
                continue

            symbol_col = "symbol" if "symbol" in raw.columns else ("ticker" if "ticker" in raw.columns else None)
            if symbol_col is None:
                continue

            chunk = raw[raw[symbol_col].astype(str).str.upper() == symbol_upper].copy()
            if chunk.empty:
                continue

            is_intraday = timeframe in INTRADAY_TIMEFRAMES
            ts_series = _normalize_intraday_ts(chunk) if is_intraday else _normalize_daily_ts(chunk)
            if ts_series is None:
                continue

            chunk["ts"] = ts_series
            chunk = chunk.dropna(subset=["ts"])
            if chunk.empty:
                continue

            required = {"open", "high", "low", "close", "volume"}
            if not required.issubset(set(chunk.columns)):
                continue

            chunk = chunk.loc[:, ["ts", "open", "high", "low", "close", "volume", "is_rth", "is_premarket", "is_postmarket"] if is_intraday else ["ts", "open", "high", "low", "close", "volume"]]
            for col in ("open", "high", "low", "close", "volume"):
                chunk[col] = pd.to_numeric(chunk[col], errors="coerce")
            chunk = chunk.dropna(subset=["open", "high", "low", "close", "volume"])
            if chunk.empty:
                continue

            if is_intraday:
                chunk = _apply_session_filter(chunk, session=session)
                if chunk.empty:
                    continue

            chunk = chunk[(chunk["ts"] >= start_ts) & (chunk["ts"] <= end_ts)]
            if chunk.empty:
                continue
            bars.append(chunk.loc[:, BAR_COLUMNS])

        if not bars:
            return _empty_bar_frame()

        frame = pd.concat(bars, ignore_index=True)
        frame = frame.sort_values("ts")
        frame = frame.drop_duplicates(subset=["ts"], keep="last")

        if self._split_mode == "splits_backward":
            split_events = self._load_split_events_for_runtime(symbol_upper)
            if split_events:
                frame["_symbol"] = symbol_upper
                try:
                    local_days = frame["ts"].dt.tz_convert(self._split_timezone).dt.date
                except Exception:
                    local_days = frame["ts"].dt.date
                frame, _, _ = apply_backward_split_adjustments(
                    frame,
                    symbol_col="_symbol",
                    local_day=local_days,
                    split_events=split_events,
                    adjust_volume=self._split_adjust_volume,
                )
                frame = frame.drop(columns=["_symbol"], errors="ignore")

        return frame.reset_index(drop=True)

    @staticmethod
    def make_trade_windows(entry_ts: object, exit_ts: object) -> dict[str, tuple[datetime, datetime]]:
        entry = _to_ny_timestamp(entry_ts)
        if entry is None:
            now = pd.Timestamp.now(tz=NY_TZ)
            start = (now - timedelta(days=1)).to_pydatetime()
            end = (now + timedelta(days=1)).to_pydatetime()
            return {
                "daily": (start, end),
                "granular": (start, end),
            }

        exit_point = _to_ny_timestamp(exit_ts)
        entry_day = entry.floor("D")
        if exit_point is not None:
            exit_day = exit_point.floor("D")
            granular_end = (exit_day + pd.Timedelta(days=1)).to_pydatetime()
        else:
            granular_end = (entry_day + pd.Timedelta(days=1)).to_pydatetime()

        daily_start = (entry - pd.offsets.BDay(60)).to_pydatetime()
        daily_end = (entry + pd.offsets.BDay(60)).to_pydatetime()
        granular_start = (entry_day - pd.Timedelta(days=1)).to_pydatetime()
        return {
            "daily": (daily_start, daily_end),
            "granular": (granular_start, granular_end),
        }

    def _read_file(self, file_path: Path) -> pd.DataFrame:
        cache_key = str(file_path.resolve())
        cached = self._file_cache.get(cache_key)
        if cached is not None:
            return cached.copy()

        try:
            frame = pd.read_parquet(file_path)
        except Exception:
            frame = pd.DataFrame()

        self._file_cache[cache_key] = frame
        while len(self._file_cache) > self.max_cache_files:
            self._file_cache.pop(next(iter(self._file_cache)), None)

        return frame.copy()

    def _load_split_events_for_runtime(self, symbol: str) -> dict[str, list]:
        if self._split_mode != "splits_backward":
            return {}
        if self._split_events_file is None:
            self._warn("Split adjustment enabled but split_events_file is not set; using raw chart bars.")
            return {}

        key = (
            self._split_mode,
            str(self._split_events_file),
            self._split_end_date.isoformat() if self._split_end_date else "",
            self._split_adjust_volume,
            self._split_timezone,
        )
        if self._split_cache_key != key:
            self._split_events_cache = None
            self._split_cache_key = key

        if self._split_events_cache is None:
            try:
                self._split_events_cache = load_split_events(
                    self._split_events_file,
                    tickers={symbol},
                    end_date=self._split_end_date,
                )
            except Exception as exc:
                self._warn(f"Split adjustment unavailable: {exc}")
                self._split_events_cache = {}

        return self._split_events_cache or {}

    def _warn(self, message: str) -> None:
        if message not in self._runtime_warnings:
            self._runtime_warnings.append(message)


def _empty_bar_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=BAR_COLUMNS)


def _to_ny_timestamp(value: object) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        ts = pd.to_datetime(value, errors="coerce", utc=True)
    except Exception:
        return None
    if pd.isna(ts):
        return None
    if not isinstance(ts, pd.Timestamp):
        return None
    return ts.tz_convert(NY_TZ)


def _iter_years(start: pd.Timestamp, end: pd.Timestamp) -> Iterable[int]:
    for year in range(start.year, end.year + 1):
        yield year


def _normalize_intraday_ts(frame: pd.DataFrame) -> pd.Series | None:
    if "ts_utc" in frame.columns:
        ts = pd.to_datetime(frame["ts_utc"], errors="coerce", utc=True)
    elif "timestamp" in frame.columns:
        ts = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    else:
        return None
    if ts.isna().all():
        return None
    return ts.dt.tz_convert(NY_TZ)


def _normalize_daily_ts(frame: pd.DataFrame) -> pd.Series | None:
    col = "session_date" if "session_date" in frame.columns else ("trading_day_est" if "trading_day_est" in frame.columns else None)
    if col is None:
        return None
    days = pd.to_datetime(frame[col], errors="coerce")
    if days.isna().all():
        return None
    if getattr(days.dt, "tz", None) is None:
        return days.dt.tz_localize(NY_TZ, ambiguous="NaT", nonexistent="NaT")
    return days.dt.tz_convert(NY_TZ)


def _apply_session_filter(frame: pd.DataFrame, *, session: str) -> pd.DataFrame:
    mode = str(session or "full").lower()
    if mode == "full":
        return frame

    if mode == "rth" and "is_rth" in frame.columns:
        return frame[frame["is_rth"].fillna(False).astype(bool)].copy()
    if mode == "premarket" and "is_premarket" in frame.columns:
        return frame[frame["is_premarket"].fillna(False).astype(bool)].copy()
    if mode == "postmarket" and "is_postmarket" in frame.columns:
        return frame[frame["is_postmarket"].fillna(False).astype(bool)].copy()

    minute = frame["ts"].dt.hour * 60 + frame["ts"].dt.minute
    if mode == "rth":
        mask = (minute >= RTH_START_MINUTE) & (minute < RTH_END_MINUTE)
    elif mode == "premarket":
        mask = minute < RTH_START_MINUTE
    elif mode == "postmarket":
        mask = minute >= RTH_END_MINUTE
    else:
        mask = pd.Series([True] * len(frame), index=frame.index)

    return frame[mask].copy()
