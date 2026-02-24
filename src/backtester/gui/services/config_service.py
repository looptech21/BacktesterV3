"""GUI service: RunConfig defaults, validation, and persistence."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from backtester.config import RunConfig
from backtester.experiment import experiment_id
from backtester.gui.state import AppState

DATASET_BASE = Path("/media/hp14linux/Daten/Backtest_data/ohlcv_test_20_tickers")
DATASET_PARQUET_BASE = DATASET_BASE / "parquet"
DEFAULT_PARQUET_ROOT = DATASET_PARQUET_BASE / "session=rth" / "timeframe=1m"
DEFAULT_PERIOD_START = date(2020, 1, 1)
DEFAULT_PERIOD_END = date(2021, 12, 31)
TIMEFRAME_ORDER = {"1m": 0, "5m": 1, "15m": 2, "1h": 3, "1d": 4}


class ConfigService:
    def __init__(self, state: AppState) -> None:
        self.state = state

    def available_parquet_roots(self) -> list[Path]:
        roots: list[Path] = []
        if DATASET_PARQUET_BASE.exists():
            for session_dir in sorted(DATASET_PARQUET_BASE.glob("session=*")):
                if not session_dir.is_dir():
                    continue
                for timeframe_dir in sorted(session_dir.glob("timeframe=*")):
                    if timeframe_dir.is_dir():
                        roots.append(timeframe_dir.resolve())
        if DEFAULT_PARQUET_ROOT.exists() and DEFAULT_PARQUET_ROOT.resolve() not in roots:
            roots.append(DEFAULT_PARQUET_ROOT.resolve())
        return sorted(roots, key=self._root_sort_key)

    def parquet_root_options(self) -> dict[str, str]:
        options: dict[str, str] = {}
        for root in self.available_parquet_roots():
            options[str(root)] = self._label_for_parquet_root(root)
        return options

    def detect_period_for_parquet_root(self, parquet_root: Path) -> tuple[date, date] | None:
        root = Path(parquet_root)
        if not root.exists():
            return None

        files = sorted(root.glob("ohlcv_*.parquet"))
        if not files:
            return None

        first_day = self._read_edge_day(files[0], find_max=False)
        last_day = self._read_edge_day(files[-1], find_max=True)

        if first_day is None:
            first_day = self._month_start_from_filename(files[0].stem)
        if last_day is None:
            last_day = self._month_end_from_filename(files[-1].stem)

        if first_day is None or last_day is None:
            return None
        if last_day < first_day:
            return None
        return first_day, last_day

    def build_default_config(self) -> RunConfig:
        roots = self.available_parquet_roots()
        if DEFAULT_PARQUET_ROOT.exists():
            parquet_root = DEFAULT_PARQUET_ROOT.resolve()
        elif roots:
            parquet_root = roots[0]
        else:
            parquet_root = Path("parquet_data")

        available_period = self.detect_period_for_parquet_root(parquet_root)
        if available_period is None:
            start_date, end_date = DEFAULT_PERIOD_START, DEFAULT_PERIOD_END
        else:
            start_date, end_date = available_period

        base = {
            "run_name": "baseline",
            "period": {"start_date": start_date, "end_date": end_date},
            "data": {
                "parquet_root": parquet_root,
                "lean_data_root": self.lean_data_root_for_parquet_root(parquet_root),
                "cache_root": self.state.output_root / "cache",
                "timezone": "America/New_York",
                "rth_start": "09:30",
                "rth_end": "16:00",
            },
            "universe": {
                "mode": "custom_list",
                "tickers": ["AAPL", "TSLA", "NVDA"],
                "filters": {"exclude_otc": True},
            },
            "output": {"dir": self.state.output_root},
        }
        return RunConfig.model_validate(base)

    def config_to_yaml(self, config: RunConfig) -> str:
        data = config.model_dump(mode="json")
        return yaml.safe_dump(data, sort_keys=False)

    def yaml_to_config(self, raw: str) -> RunConfig:
        data = yaml.safe_load(raw) or {}
        if not isinstance(data, dict):
            raise ValueError("YAML must parse to a mapping at the top level")
        return RunConfig.model_validate(data)

    def save_named_config(self, name: str, config: RunConfig) -> int:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Config name must not be empty")
        exp_id = experiment_id(config)
        yaml_text = self.config_to_yaml(config)
        return self.state.db.save_config(clean_name, exp_id, yaml_text)

    def list_saved_configs(self) -> list[dict[str, Any]]:
        return self.state.db.list_configs()

    def load_saved_config(self, config_id: int) -> RunConfig:
        raw = self.state.db.get_config_yaml(config_id)
        if raw is None:
            raise ValueError(f"No saved config with id={config_id}")
        return self.yaml_to_config(raw)

    def lean_data_root_for_parquet_root(self, parquet_root: Path) -> Path:
        session, timeframe = self.session_timeframe_from_path(Path(parquet_root))
        return self.state.output_root / "lean_data" / f"session_{session}_timeframe_{timeframe}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _root_sort_key(path: Path) -> tuple[str, int, str]:
        session = "unknown"
        timeframe = "unknown"
        for part in path.parts:
            if part.startswith("session="):
                session = part.split("=", 1)[1]
            if part.startswith("timeframe="):
                timeframe = part.split("=", 1)[1]
        return session, TIMEFRAME_ORDER.get(timeframe, 99), str(path)

    @staticmethod
    def _label_for_parquet_root(path: Path) -> str:
        session, timeframe = ConfigService.session_timeframe_from_path(path)
        return f"{session} / {timeframe}"

    @staticmethod
    def session_timeframe_from_path(path: Path) -> tuple[str, str]:
        session = "custom"
        timeframe = "custom"
        for part in path.parts:
            if part.startswith("session="):
                session = part.split("=", 1)[1]
            if part.startswith("timeframe="):
                timeframe = part.split("=", 1)[1]
        return session, timeframe

    @staticmethod
    def _read_edge_day(file_path: Path, *, find_max: bool) -> date | None:
        try:
            sample = pd.read_parquet(file_path)
        except Exception:
            return None
        if sample.empty:
            return None

        if "trading_day_est" in sample.columns:
            values = pd.to_datetime(sample["trading_day_est"], errors="coerce")
            values = values.dropna()
            if values.empty:
                return None
            return values.max().date() if find_max else values.min().date()

        if "timestamp" in sample.columns:
            values = pd.to_datetime(sample["timestamp"], errors="coerce", utc=True).dropna()
            if values.empty:
                return None
            values_est = values.dt.tz_convert("America/New_York")
            return values_est.max().date() if find_max else values_est.min().date()

        return None

    @staticmethod
    def _month_start_from_filename(stem: str) -> date | None:
        prefix = "ohlcv_"
        if not stem.startswith(prefix):
            return None
        ym = stem[len(prefix):]
        try:
            year_text, month_text = ym.split("-")
            return date(int(year_text), int(month_text), 1)
        except Exception:
            return None

    @staticmethod
    def _month_end_from_filename(stem: str) -> date | None:
        start = ConfigService._month_start_from_filename(stem)
        if start is None:
            return None
        last_day = monthrange(start.year, start.month)[1]
        return date(start.year, start.month, last_day)
