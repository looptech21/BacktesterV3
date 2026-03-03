"""GUI service: RunConfig defaults, validation, and persistence."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from backtester.config import RunConfig
from backtester.experiment import experiment_id
from backtester.gui.state import AppState

DATASET_ROOT = Path("/mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d")
DATASET_PARQUET_BASE = DATASET_ROOT / "bars"
DEFAULT_PARQUET_ROOT = DATASET_PARQUET_BASE / "5m"
DEFAULT_PERIOD_START = date(2018, 1, 1)
DEFAULT_PERIOD_END = date(2025, 12, 31)
TIMEFRAME_ORDER = {"1m": 0, "5m": 1, "15m": 2, "1h": 3, "1d": 4}


class ConfigService:
    def __init__(self, state: AppState) -> None:
        self.state = state

    def available_parquet_roots(self) -> list[Path]:
        roots: list[Path] = []
        if DATASET_PARQUET_BASE.exists():
            for timeframe_dir in sorted(DATASET_PARQUET_BASE.iterdir()):
                if not timeframe_dir.is_dir():
                    continue
                if timeframe_dir.name not in TIMEFRAME_ORDER:
                    continue
                if list(timeframe_dir.glob("symbol=*/year=*.parquet")):
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
        if not root.exists() or not root.is_dir():
            return None

        from_manifests = self._detect_period_from_manifests(root)
        if from_manifests is not None:
            return from_manifests

        years: set[int] = set()
        for path in root.glob("symbol=*/year=*.parquet"):
            try:
                year = int(path.stem.split("=", 1)[1])
            except Exception:
                continue
            years.add(year)

        if not years:
            return None

        return date(min(years), 1, 1), date(max(years), 12, 31)

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
                "source_layout": "symbol_year",
                "lean_feed_scope": "full",
                "split_adjustment_mode": "none",
            },
            "universe": {
                "mode": "custom_list",
                "tickers": ["AAPL", "TSLA", "NVDA"],
                "filters": {"exclude_otc": True},
            },
            "execution": {
                "order_session_scope": "rth_only",
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
        _, timeframe = self.session_timeframe_from_path(Path(parquet_root))
        return self.state.output_root / "lean_data" / f"timeframe_{timeframe}"

    def _detect_period_from_manifests(self, parquet_root: Path) -> tuple[date, date] | None:
        dataset_root = self._dataset_root_from_parquet_root(parquet_root)
        if dataset_root is None:
            return None

        eligibility_path = dataset_root / "manifests" / "universe" / "eligibility_summary.json"
        if eligibility_path.exists():
            try:
                payload = json.loads(eligibility_path.read_text(encoding="utf-8"))
                start_text = str(payload.get("start_date") or "")
                per_year = payload.get("per_year") or []
                years = []
                for row in per_year:
                    if isinstance(row, dict) and "year" in row:
                        years.append(int(row["year"]))
                if start_text and years:
                    start_day = date.fromisoformat(start_text)
                    end_day = date(max(years), 12, 31)
                    if end_day >= start_day:
                        return start_day, end_day
            except Exception:
                pass

        build_manifest_path = dataset_root / "manifests" / "build_manifest.json"
        if build_manifest_path.exists():
            try:
                payload = json.loads(build_manifest_path.read_text(encoding="utf-8"))
                horizon = str(payload.get("split_horizon_end_date") or "")
                if horizon:
                    end_day = date.fromisoformat(horizon)
                    return DEFAULT_PERIOD_START, end_day
            except Exception:
                pass

        return None

    @staticmethod
    def _dataset_root_from_parquet_root(parquet_root: Path) -> Path | None:
        root = Path(parquet_root)
        if root.name in TIMEFRAME_ORDER and root.parent.name == "bars":
            return root.parent.parent
        if root.name == "bars":
            return root.parent
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _root_sort_key(path: Path) -> tuple[int, str]:
        timeframe = path.name if path.name in TIMEFRAME_ORDER else "unknown"
        return TIMEFRAME_ORDER.get(timeframe, 99), str(path)

    @staticmethod
    def _label_for_parquet_root(path: Path) -> str:
        _, timeframe = ConfigService.session_timeframe_from_path(path)
        return f"full / {timeframe}"

    @staticmethod
    def session_timeframe_from_path(path: Path) -> tuple[str, str]:
        # Strict new layout only: .../bars/<timeframe>
        if path.name in TIMEFRAME_ORDER:
            return "full", path.name
        return "full", "custom"
