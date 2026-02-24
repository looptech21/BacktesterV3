"""Parameter sweep grid expansion.

Core logic from BacktesterV2's sweep_runner.py — the most elegant part of the system.
Dotted-path overrides + Cartesian product = powerful parameter variation in ~30 LOC.
"""

from __future__ import annotations

import copy
import itertools
from typing import Any

import yaml

from backtester.config import RunConfig, SweepConfig, load_run_config


def set_deep(data: dict[str, Any], dotted_key: str, value: Any) -> None:
    """Set a value in a nested dict using a dotted path (e.g., 'setups.common_breakout.orh_window_minutes')."""
    parts = dotted_key.split(".")
    target = data
    for part in parts[:-1]:
        if part not in target or not isinstance(target[part], dict):
            target[part] = {}
        target = target[part]
    target[parts[-1]] = value


def grid_combinations(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Expand a grid of {dotted_path: [values]} into all Cartesian product combinations."""
    if not grid:
        return [{}]
    keys = sorted(grid.keys())
    all_values = [grid[k] for k in keys]
    return [{k: v for k, v in zip(keys, vals)} for vals in itertools.product(*all_values)]


def expand_sweep(sweep: SweepConfig, base: RunConfig) -> list[tuple[str, dict[str, Any], RunConfig]]:
    """Expand a sweep config into a list of (run_name, combo_dict, RunConfig) tuples."""
    base_dict = base.model_dump(mode="json")
    combos = grid_combinations(sweep.grid.values)
    results = []

    for i, combo in enumerate(combos, start=1):
        conf = copy.deepcopy(base_dict)
        run_name = f"{sweep.run_prefix}_{i:03d}"
        conf["run_name"] = run_name

        for key, value in combo.items():
            set_deep(conf, key, value)

        run_config = RunConfig.model_validate(conf)
        results.append((run_name, combo, run_config))

    return results


def write_sweep_configs(
    sweep: SweepConfig,
    base: RunConfig,
    output_dir: "Path",
) -> list[tuple[str, dict[str, Any], "Path"]]:
    """Write individual YAML configs for each sweep combination. Returns (run_name, combo, config_path) tuples."""
    from pathlib import Path

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    expanded = expand_sweep(sweep, base)
    results = []

    for run_name, combo, run_config in expanded:
        conf_path = output_dir / f"{run_name}.yaml"
        conf_dict = run_config.model_dump(mode="json")
        conf_path.write_text(yaml.safe_dump(conf_dict, sort_keys=True), encoding="utf-8")
        results.append((run_name, combo, conf_path))

    return results
