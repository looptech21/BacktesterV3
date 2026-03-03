from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

import backtester.gui.services.config_service as config_service_module
from backtester.gui.services.config_service import ConfigService
from backtester.gui.state import init_app_state


def _write_symbol_year(path: Path, *, symbol: str, year: int) -> None:
    target = path / f"symbol={symbol}" / f"year={year}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "symbol": symbol,
                "ts_utc": f"{year}-01-02T14:30:00+00:00",
                "session_date": f"{year}-01-02",
                "is_rth": True,
                "is_premarket": False,
                "is_postmarket": False,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000.0,
            }
        ]
    ).to_parquet(target, index=False)


def test_build_default_config_prefills_detected_parquet_root(tmp_path, monkeypatch):
    detected_root = tmp_path / "dataset" / "bars" / "5m"
    _write_symbol_year(detected_root, symbol="AAPL", year=2024)

    monkeypatch.setattr(config_service_module, "DEFAULT_PARQUET_ROOT", detected_root)
    monkeypatch.setattr(config_service_module, "DATASET_PARQUET_BASE", detected_root.parent)

    state = init_app_state(tmp_path / "outputs")
    service = ConfigService(state)
    config = service.build_default_config()

    assert Path(config.data.parquet_root) == detected_root
    assert config.period.start_date == date(2024, 1, 1)
    assert config.period.end_date == date(2024, 12, 31)
    assert config.data.source_layout == "symbol_year"
    assert config.data.lean_feed_scope == "full"
    assert config.execution.order_session_scope == "rth_only"
    assert config.data.split_adjustment_mode == "none"


def test_config_yaml_round_trip_and_persistence(tmp_path):
    state = init_app_state(tmp_path / "outputs")
    service = ConfigService(state)

    config = service.build_default_config()
    raw = service.config_to_yaml(config)
    parsed = service.yaml_to_config(raw)
    assert parsed.model_dump(mode="json") == config.model_dump(mode="json")

    cfg_id = service.save_named_config("baseline", parsed)
    assert cfg_id > 0
    loaded = service.load_saved_config(cfg_id)
    assert loaded.model_dump(mode="json") == parsed.model_dump(mode="json")


def test_detect_period_for_parquet_root_uses_manifest_summary(tmp_path):
    dataset_root = tmp_path / "dataset"
    bars_root = dataset_root / "bars" / "5m"
    _write_symbol_year(bars_root, symbol="AAPL", year=2023)

    summary_path = dataset_root / "manifests" / "universe" / "eligibility_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            {
                "start_date": "2018-01-01",
                "per_year": [{"year": 2018}, {"year": 2025}],
            }
        ),
        encoding="utf-8",
    )

    state = init_app_state(tmp_path / "outputs")
    service = ConfigService(state)
    period = service.detect_period_for_parquet_root(bars_root)

    assert period == (date(2018, 1, 1), date(2025, 12, 31))


def test_detect_period_for_parquet_root_falls_back_to_year_files(tmp_path):
    bars_root = tmp_path / "dataset" / "bars" / "15m"
    _write_symbol_year(bars_root, symbol="AAPL", year=2019)
    _write_symbol_year(bars_root, symbol="AAPL", year=2021)

    state = init_app_state(tmp_path / "outputs")
    service = ConfigService(state)
    period = service.detect_period_for_parquet_root(bars_root)

    assert period == (date(2019, 1, 1), date(2021, 12, 31))


def test_parquet_root_options_discovers_bars_timeframes(tmp_path, monkeypatch):
    bars_base = tmp_path / "dataset" / "bars"
    _write_symbol_year(bars_base / "5m", symbol="AAPL", year=2024)
    _write_symbol_year(bars_base / "1h", symbol="TSLA", year=2024)
    (bars_base / "junk").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config_service_module, "DATASET_PARQUET_BASE", bars_base)
    monkeypatch.setattr(config_service_module, "DEFAULT_PARQUET_ROOT", bars_base / "5m")

    state = init_app_state(tmp_path / "outputs")
    service = ConfigService(state)
    options = service.parquet_root_options()

    assert len(options) == 2
    assert "full / 5m" in options.values()
    assert "full / 1h" in options.values()


def test_lean_data_root_namespaced_by_timeframe(tmp_path):
    state = init_app_state(tmp_path / "outputs")
    service = ConfigService(state)

    source_root = Path("/tmp/data/bars/15m")
    lean_root = service.lean_data_root_for_parquet_root(source_root)

    assert str(lean_root).endswith("outputs/lean_data/timeframe_15m")
