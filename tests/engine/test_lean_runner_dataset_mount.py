from __future__ import annotations

import json
from pathlib import Path

from backtester.config import RunConfig
from backtester.engine.lean_runner import (
    _dataset_mount_for_parquet_root,
    _lean_runtime_config_payload,
    run_lean_docker,
)


def _config_with_parquet_root(tmp_path: Path, parquet_root: Path | str) -> RunConfig:
    return RunConfig.model_validate(
        {
            "run_name": "lean_mount_test",
            "period": {"start_date": "2024-01-01", "end_date": "2024-01-31"},
            "data": {
                "parquet_root": str(parquet_root),
                "lean_data_root": str(tmp_path / "lean_data"),
            },
            "universe": {"mode": "custom_list", "tickers": ["AAPL"]},
            "output": {"dir": str(tmp_path / "outputs")},
        }
    )


def test_dataset_mount_for_symbol_year_timeframe_path(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    parquet_root = Path("/mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d/bars/5m")
    mount_root, container_root = _dataset_mount_for_parquet_root(parquet_root, repo_root)

    assert mount_root == Path("/mnt/Daten/Backtest_data/processed_splitadjusted_OHLCV_1m5m15m1h1d")
    assert container_root == "/Lean/PreparedDataset/bars/5m"


def test_dataset_mount_for_relative_timeframe_path(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    parquet_root = Path("storage/smoke_dataset/bars/15m")
    mount_root, container_root = _dataset_mount_for_parquet_root(parquet_root, repo_root)

    assert mount_root == (repo_root / "storage" / "smoke_dataset")
    assert container_root == "/Lean/PreparedDataset/bars/15m"


def test_lean_runtime_payload_rewrites_parquet_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    dataset_root = tmp_path / "dataset"
    parquet_root = dataset_root / "bars" / "5m"
    parquet_root.mkdir(parents=True)

    config = _config_with_parquet_root(tmp_path, parquet_root)
    payload, mount_root = _lean_runtime_config_payload(config, repo_root)

    assert payload["data"]["parquet_root"] == "/Lean/PreparedDataset/bars/5m"
    assert mount_root == dataset_root
    assert str(config.data.parquet_root) == str(parquet_root)


def test_run_lean_docker_dry_run_uses_container_parquet_root(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    dataset_root = tmp_path / "dataset"
    parquet_root = dataset_root / "bars" / "5m"
    parquet_root.mkdir(parents=True)

    config = _config_with_parquet_root(tmp_path, parquet_root)

    monkeypatch.setattr("backtester.engine.lean_runner.is_docker_available", lambda: True)

    result = run_lean_docker(config, run_dir, "exp_dry", repo_root, dry_run=True)

    assert result.status == "dry_run"
    assert result.lean_config_path is not None

    lean_cfg = json.loads(result.lean_config_path.read_text(encoding="utf-8"))
    cfg_payload = json.loads(lean_cfg["parameters"]["config-json"])
    assert cfg_payload["data"]["parquet_root"] == "/Lean/PreparedDataset/bars/5m"
