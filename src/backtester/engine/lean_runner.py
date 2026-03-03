"""LEAN engine execution via Docker or local dotnet.

Simplified from BacktesterV2/engine_runner/docker_runner.py and runner_selector.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backtester.config import RunConfig


class RunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class LeanResult:
    status: str
    runner: str
    returncode: int
    log_path: Path | None
    lean_config_path: Path | None
    error: str | None = None


def is_docker_available() -> bool:
    return shutil.which("docker") is not None


def _launcher_probe_script() -> str:
    return """
set -euo pipefail

if [ -d /Lean/HostData/equity ]; then
  mkdir -p /Lean/Data
  rm -rf /Lean/Data/equity
  ln -s /Lean/HostData/equity /Lean/Data/equity
fi

mkdir -p /Lean/Data/equity/usa/map_files
mkdir -p /Lean/Data/equity/usa/factor_files

launcher="${LEAN_LAUNCHER_DLL:-}"
if [ -z "${launcher}" ]; then
  for p in \
    /Lean/Launcher/bin/Release/net6.0/QuantConnect.Lean.Launcher.dll \
    /Lean/Launcher/bin/Debug/net6.0/QuantConnect.Lean.Launcher.dll \
    /Lean/Launcher/bin/Release/QuantConnect.Lean.Launcher.dll \
    /Lean/Launcher/bin/Debug/QuantConnect.Lean.Launcher.dll \
    /Lean/Launcher/QuantConnect.Lean.Launcher.dll
  do
    if [ -f "$p" ]; then launcher="$p"; break; fi
  done
fi
if [ -z "${launcher}" ]; then
  launcher="$(find /Lean -name QuantConnect.Lean.Launcher.dll 2>/dev/null | head -n 1 || true)"
fi
if [ -z "${launcher}" ]; then
  echo "QuantConnect.Lean.Launcher.dll not found in container" >&2
  exit 2
fi
dotnet "${launcher}" --config /Lean/Results/lean_config.json
""".strip()


def _normalize_algorithm_file(raw: str) -> str:
    value = str(raw).replace("\\", "/").strip().lstrip("/")
    prefixes = (
        "src/backtester/engine/algorithm/",
        "backtester/engine/algorithm/",
        "engine/algorithm/",
        "algorithm/",
    )
    for prefix in prefixes:
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    return value or "QM_MVP.py"


_TIMEFRAME_DIRS = {"1m", "5m", "15m", "1h", "1d"}


def _resolve_host_path(path: Path, repo_root: Path) -> Path:
    target = Path(path)
    if not target.is_absolute():
        target = (repo_root / target).resolve()
    else:
        target = target.resolve()
    return target


def _dataset_mount_for_parquet_root(parquet_root: Path, repo_root: Path) -> tuple[Path, str]:
    host_root = _resolve_host_path(Path(parquet_root), repo_root)

    if host_root.name in _TIMEFRAME_DIRS and host_root.parent.name == "bars":
        dataset_root = host_root.parent.parent
        return dataset_root, f"/Lean/PreparedDataset/bars/{host_root.name}"

    if host_root.name == "bars":
        dataset_root = host_root.parent
        return dataset_root, "/Lean/PreparedDataset/bars"

    dataset_root = host_root.parent
    return dataset_root, f"/Lean/PreparedDataset/{host_root.name}"


def _lean_runtime_config_payload(config: RunConfig, repo_root: Path) -> tuple[dict[str, Any], Path]:
    dataset_mount_root, parquet_root_in_container = _dataset_mount_for_parquet_root(
        Path(config.data.parquet_root),
        repo_root,
    )

    payload = config.model_dump(mode="json")
    data_payload = dict(payload.get("data") or {})
    data_payload["parquet_root"] = parquet_root_in_container
    payload["data"] = data_payload
    return payload, dataset_mount_root


def build_lean_config(
    config: RunConfig,
    run_dir: Path,
    experiment_id: str,
    repo_root: Path,
    *,
    config_json_payload: dict[str, Any] | None = None,
) -> Path:
    """Build a LEAN configuration JSON and write it to run_dir."""
    algorithm_file = _normalize_algorithm_file(config.runner.algorithm_file)
    lean_cfg = {
        "environment": "backtesting",
        "algorithm-type-name": "QM_MVP",
        "algorithm-language": "Python",
        "algorithm-location": f"/workspace/src/backtester/engine/algorithm/{algorithm_file}",
        "data-folder": "/Lean/Data",
        "results-destination-folder": "/Lean/Results",
        "parameters": {
            "experiment-id": experiment_id,
            "start-date": str(config.period.start_date),
            "end-date": str(config.period.end_date),
            "tickers": ",".join(config.resolved_tickers()),
            "config-json": json.dumps(config_json_payload or config.model_dump(mode="json"), default=str),
        },
    }
    cfg_path = run_dir / "lean_config.json"
    cfg_path.write_text(json.dumps(lean_cfg, indent=2, default=str), encoding="utf-8")
    return cfg_path


def run_lean_docker(
    config: RunConfig,
    run_dir: Path,
    experiment_id: str,
    repo_root: Path,
    *,
    dry_run: bool = False,
) -> LeanResult:
    """Execute LEAN via Docker."""
    if not is_docker_available():
        return LeanResult(
            status="unavailable",
            runner="docker",
            returncode=-1,
            log_path=None,
            lean_config_path=None,
            error="Docker binary not found. Install Docker or use runner.mode=dotnet.",
        )

    lean_payload, prepared_dataset_root = _lean_runtime_config_payload(config, repo_root)
    lean_cfg_path = build_lean_config(
        config,
        run_dir,
        experiment_id,
        repo_root,
        config_json_payload=lean_payload,
    )
    lean_data_root = Path(config.data.lean_data_root).resolve()
    lean_data_root.mkdir(parents=True, exist_ok=True)

    script = _launcher_probe_script()
    cmd = [
        "docker", "run", "--rm",
        "--entrypoint", "bash",
        "-w", "/workspace",
        "-e", "PYTHONPATH=/workspace/src:/workspace",
        "-v", f"{repo_root.resolve()}:/workspace",
        "-v", f"{prepared_dataset_root.resolve()}:/Lean/PreparedDataset",
        "-v", f"{lean_data_root}:/Lean/HostData",
        "-v", f"{run_dir.resolve()}:/Lean/Results",
        config.runner.docker_image,
        "-lc", script,
    ]

    if dry_run:
        return LeanResult(
            status="dry_run",
            runner="docker",
            returncode=0,
            log_path=None,
            lean_config_path=lean_cfg_path,
        )

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    log_path = run_dir / "lean_runner.log"
    log_path.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
    error_text = None
    if proc.returncode != 0:
        error_source = proc.stderr if proc.stderr.strip() else proc.stdout
        error_text = error_source[-2000:]

    return LeanResult(
        status="ok" if proc.returncode == 0 else "failed",
        runner="docker",
        returncode=proc.returncode,
        log_path=log_path,
        lean_config_path=lean_cfg_path,
        error=error_text,
    )


def run_lean(
    config: RunConfig,
    run_dir: Path,
    experiment_id: str,
    repo_root: Path,
    *,
    dry_run: bool = False,
) -> LeanResult:
    """Run LEAN using configured mode (auto tries Docker first)."""
    mode = config.runner.mode

    if mode == "docker":
        result = run_lean_docker(config, run_dir, experiment_id, repo_root, dry_run=dry_run)
        if result.status in ("ok", "dry_run"):
            return result
        raise RunnerError(f"Docker mode failed: {result.error}")

    if mode == "auto":
        result = run_lean_docker(config, run_dir, experiment_id, repo_root, dry_run=dry_run)
        if result.status in ("ok", "dry_run"):
            return result
        # Auto mode: only mark skipped when Docker is not available.
        if result.status == "unavailable":
            return LeanResult(
                status="skipped",
                runner="auto",
                returncode=-1,
                log_path=None,
                lean_config_path=None,
                error="No LEAN runner available. Install Docker.",
            )
        # Preserve actual Docker execution failures as errors.
        return result

    return LeanResult(
        status="skipped",
        runner=mode,
        returncode=-1,
        log_path=None,
        lean_config_path=None,
        error=f"Runner mode '{mode}' not yet supported in V3.",
    )
