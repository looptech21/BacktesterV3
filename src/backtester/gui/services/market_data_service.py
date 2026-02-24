"""GUI service: discover and load preprocessed market bars for trade inspection."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from backtester.gui.services.config_service import DATASET_PARQUET_BASE, TIMEFRAME_ORDER

NY_TZ = "America/New_York"
BAR_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


class MarketDataService:
    """Read-only access to prepared parquet bars for chart inspection."""

    def __init__(self, parquet_base: Path | None = None, *, max_cache_files: int = 64) -> None:
        self.parquet_base = Path(parquet_base or DATASET_PARQUET_BASE)
        self.max_cache_files = max(8, int(max_cache_files))
        self._catalog_cache: dict[str, list[str]] | None = None
        self._month_cache: dict[str, pd.DataFrame] = {}

    def set_parquet_base(self, parquet_base: Path) -> None:
        target = Path(parquet_base)
        if target == self.parquet_base:
            return
        self.parquet_base = target
        self._catalog_cache = None

    def discover_catalog(self) -> dict[str, list[str]]:
        if self._catalog_cache is not None:
            return {session: list(timeframes) for session, timeframes in self._catalog_cache.items()}

        catalog: dict[str, list[str]] = {}
        if self.parquet_base.exists():
            for session_dir in sorted(self.parquet_base.glob("session=*")):
                if not session_dir.is_dir():
                    continue
                session = session_dir.name.split("=", 1)[1]
                timeframes: list[str] = []
                for timeframe_dir in sorted(session_dir.glob("timeframe=*")):
                    if not timeframe_dir.is_dir():
                        continue
                    timeframe = timeframe_dir.name.split("=", 1)[1]
                    timeframes.append(timeframe)
                if timeframes:
                    catalog[session] = sorted(set(timeframes), key=lambda tf: TIMEFRAME_ORDER.get(tf, 99))

        self._catalog_cache = catalog
        return {session: list(timeframes) for session, timeframes in catalog.items()}

    def resolve_parquet_root(self, session: str, timeframe: str) -> Path | None:
        root = self.parquet_base / f"session={session}" / f"timeframe={timeframe}"
        if root.exists():
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

        bars: list[pd.DataFrame] = []
        symbol_upper = str(symbol or "").upper().strip()
        if not symbol_upper:
            return _empty_bar_frame()

        for month_text in _iter_month_keys(start_ts, end_ts):
            month_file = root / f"ohlcv_{month_text}.parquet"
            if not month_file.exists():
                continue

            raw = self._read_month(month_file)
            if raw.empty or "ticker" not in raw.columns:
                continue

            chunk = raw[raw["ticker"].astype(str).str.upper() == symbol_upper].copy()
            if chunk.empty:
                continue

            ts_series = _normalize_ts(chunk, timeframe)
            if ts_series is None:
                continue

            chunk["ts"] = ts_series
            chunk = chunk.dropna(subset=["ts"])
            if chunk.empty:
                continue

            required = {"open", "high", "low", "close", "volume"}
            if not required.issubset(set(chunk.columns)):
                continue

            chunk = chunk.loc[:, ["ts", "open", "high", "low", "close", "volume"]]
            for col in ("open", "high", "low", "close", "volume"):
                chunk[col] = pd.to_numeric(chunk[col], errors="coerce")
            chunk = chunk.dropna(subset=["open", "high", "low", "close", "volume"])
            if chunk.empty:
                continue

            chunk = chunk[(chunk["ts"] >= start_ts) & (chunk["ts"] <= end_ts)]
            if chunk.empty:
                continue
            bars.append(chunk)

        if not bars:
            return _empty_bar_frame()

        frame = pd.concat(bars, ignore_index=True)
        frame = frame.sort_values("ts")
        frame = frame.drop_duplicates(subset=["ts"], keep="last")
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

    def _read_month(self, month_file: Path) -> pd.DataFrame:
        cache_key = str(month_file.resolve())
        cached = self._month_cache.get(cache_key)
        if cached is not None:
            return cached.copy()

        try:
            frame = pd.read_parquet(month_file)
        except Exception:
            frame = pd.DataFrame()

        self._month_cache[cache_key] = frame
        while len(self._month_cache) > self.max_cache_files:
            self._month_cache.pop(next(iter(self._month_cache)), None)

        return frame.copy()


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


def _iter_month_keys(start: pd.Timestamp, end: pd.Timestamp) -> Iterable[str]:
    cursor = pd.Timestamp(year=start.year, month=start.month, day=1, tz=NY_TZ)
    finish = pd.Timestamp(year=end.year, month=end.month, day=1, tz=NY_TZ)
    while cursor <= finish:
        yield cursor.strftime("%Y-%m")
        cursor = cursor + pd.offsets.MonthBegin(1)


def _normalize_ts(frame: pd.DataFrame, timeframe: str) -> pd.Series | None:
    if timeframe == "1d" or "timestamp" not in frame.columns:
        if "trading_day_est" not in frame.columns:
            return None
        days = pd.to_datetime(frame["trading_day_est"], errors="coerce")
        if days.isna().all():
            return None
        tz = getattr(days.dt, "tz", None)
        if tz is None:
            return days.dt.tz_localize(NY_TZ, ambiguous="NaT", nonexistent="NaT")
        return days.dt.tz_convert(NY_TZ)

    ts = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    if ts.isna().all():
        return None
    return ts.dt.tz_convert(NY_TZ)
