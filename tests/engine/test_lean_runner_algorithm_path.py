from __future__ import annotations

import json
from pathlib import Path

from backtester.config import RunConfig
from backtester.engine.lean_runner import build_lean_config


def _config_with_algorithm(tmp_path: Path, algorithm_file: str) -> RunConfig:
    return RunConfig.model_validate(
        {
            "run_name": "algo_path_test",
            "period": {"start_date": "2020-01-01", "end_date": "2020-01-31"},
            "data": {"parquet_root": str(tmp_path / "parquet")},
            "universe": {"mode": "custom_list", "tickers": ["AAPL"]},
            "runner": {"algorithm_file": algorithm_file},
            "output": {"dir": str(tmp_path / "outputs")},
        }
    )


def test_build_lean_config_accepts_both_algorithm_path_forms(tmp_path):
    run_dir = tmp_path / "run_dir"
    run_dir.mkdir(parents=True, exist_ok=True)

    config_plain = _config_with_algorithm(tmp_path, "QM_MVP.py")
    path_plain = build_lean_config(config_plain, run_dir, "exp_plain", tmp_path)
    plain_data = json.loads(path_plain.read_text(encoding="utf-8"))
    assert plain_data["algorithm-location"] == "/workspace/src/backtester/engine/algorithm/QM_MVP.py"

    config_prefixed = _config_with_algorithm(tmp_path, "algorithm/QM_MVP.py")
    path_prefixed = build_lean_config(config_prefixed, run_dir, "exp_prefixed", tmp_path)
    prefixed_data = json.loads(path_prefixed.read_text(encoding="utf-8"))
    assert prefixed_data["algorithm-location"] == "/workspace/src/backtester/engine/algorithm/QM_MVP.py"

