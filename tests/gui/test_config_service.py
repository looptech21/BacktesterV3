from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import backtester.gui.services.config_service as config_service_module
from backtester.gui.services.config_service import ConfigService
from backtester.gui.state import init_app_state


def test_build_default_config_prefills_detected_parquet_root(tmp_path, monkeypatch):
    detected_root = tmp_path / "detected_parquet"
    detected_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_service_module, "DEFAULT_PARQUET_ROOT", detected_root)

    state = init_app_state(tmp_path / "outputs")
    service = ConfigService(state)
    config = service.build_default_config()

    assert Path(config.data.parquet_root) == detected_root
    assert config.period.start_date == date(2020, 1, 1)
    assert config.universe.tickers == ["AAPL", "NVDA", "TSLA"]


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


def test_yaml_to_config_rejects_invalid_payload(tmp_path):
    state = init_app_state(tmp_path / "outputs")
    service = ConfigService(state)

    invalid_yaml = """
run_name: invalid
data:
  parquet_root: /tmp/whatever
"""
    try:
        service.yaml_to_config(invalid_yaml)
    except Exception as exc:
        assert "period" in str(exc).lower() or "universe" in str(exc).lower()
    else:
        raise AssertionError("Expected invalid YAML payload to fail validation")


def test_detect_period_for_parquet_root_uses_data_edges(tmp_path):
    parquet_root = tmp_path / "parquet" / "session=rth" / "timeframe=1m"
    parquet_root.mkdir(parents=True, exist_ok=True)

    first = pd.DataFrame(
        {
            "timestamp": [
                "2019-06-03T13:30:00+00:00",
                "2019-06-28T19:59:00+00:00",
            ],
            "ticker": ["AAPL", "AAPL"],
            "open": [1.0, 2.0],
            "high": [1.1, 2.1],
            "low": [0.9, 1.9],
            "close": [1.0, 2.0],
            "volume": [100, 200],
            "trading_day_est": ["2019-06-03", "2019-06-28"],
        }
    )
    last = pd.DataFrame(
        {
            "timestamp": [
                "2021-05-03T13:30:00+00:00",
                "2021-05-31T19:59:00+00:00",
            ],
            "ticker": ["AAPL", "AAPL"],
            "open": [1.0, 2.0],
            "high": [1.1, 2.1],
            "low": [0.9, 1.9],
            "close": [1.0, 2.0],
            "volume": [100, 200],
            "trading_day_est": ["2021-05-03", "2021-05-31"],
        }
    )
    first.to_parquet(parquet_root / "ohlcv_2019-06.parquet", index=False)
    last.to_parquet(parquet_root / "ohlcv_2021-05.parquet", index=False)

    state = init_app_state(tmp_path / "outputs")
    service = ConfigService(state)
    period = service.detect_period_for_parquet_root(parquet_root)

    assert period is not None
    assert period[0] == date(2019, 6, 3)
    assert period[1] == date(2021, 5, 31)


def test_parquet_root_options_discovers_session_timeframe_paths(tmp_path, monkeypatch):
    parquet_base = tmp_path / "dataset" / "parquet"
    (parquet_base / "session=rth" / "timeframe=1m").mkdir(parents=True, exist_ok=True)
    (parquet_base / "session=rth" / "timeframe=5m").mkdir(parents=True, exist_ok=True)
    (parquet_base / "session=full" / "timeframe=1d").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config_service_module, "DATASET_PARQUET_BASE", parquet_base)
    monkeypatch.setattr(
        config_service_module,
        "DEFAULT_PARQUET_ROOT",
        parquet_base / "session=rth" / "timeframe=1m",
    )

    state = init_app_state(tmp_path / "outputs")
    service = ConfigService(state)
    options = service.parquet_root_options()

    assert len(options) == 3
    assert "full / 1d" in options.values()
    assert "rth / 1m" in options.values()
    assert "rth / 5m" in options.values()


def test_lean_data_root_namespaced_by_session_and_timeframe(tmp_path):
    state = init_app_state(tmp_path / "outputs")
    service = ConfigService(state)

    source_root = Path("/tmp/data/parquet/session=full/timeframe=15m")
    lean_root = service.lean_data_root_for_parquet_root(source_root)

    assert str(lean_root).endswith("outputs/lean_data/session_full_timeframe_15m")
