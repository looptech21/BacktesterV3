"""Parse LEAN output JSON into normalized fill records.

Ported from BacktesterV2/reporting/lean_ingest.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _normalize_fill(raw: dict[str, Any]) -> dict[str, Any]:
    raw_qty = _safe_int(raw.get("qty", raw.get("fill_quantity", 0)))
    side = str(raw.get("side", "")).upper()
    if not side:
        side = "SELL" if raw_qty < 0 else "BUY"
    role = str(raw.get("role", "")).lower()
    if role not in {"entry", "exit"}:
        role = "entry" if side == "BUY" else "exit"

    normalized_qty = abs(raw_qty) if raw_qty != 0 else abs(_safe_int(raw.get("fill_quantity", 0)))
    if normalized_qty == 0:
        normalized_qty = abs(_safe_int(raw.get("entry_qty", 0)))

    return {
        "experiment_id": str(raw.get("experiment_id", "")),
        "order_id": _safe_int(raw.get("order_id", 0)),
        "symbol": str(raw.get("symbol", "")).upper(),
        "side": side,
        "role": role,
        "setup_type": str(raw.get("setup_type", "unknown")),
        "setup_lot_id": str(raw.get("setup_lot_id", "")),
        "entry_stage": _safe_int(raw.get("entry_stage", 0)),
        "stop_mode": str(raw.get("stop_mode", "")),
        "scan_day": str(raw.get("scan_day", "")),
        "trade_day": str(raw.get("trade_day", "")),
        "order_type": str(raw.get("order_type", "MARKET")),
        "signal_ts_utc": str(raw.get("signal_ts_utc", raw.get("entry_signal_ts_utc", ""))),
        "submit_ts_utc": str(raw.get("submit_ts_utc", raw.get("signal_ts_utc", ""))),
        "fill_ts_utc": str(raw.get("fill_ts_utc", raw.get("entry_fill_ts_utc", ""))),
        "qty": normalized_qty,
        "fill_price": _safe_float(raw.get("fill_price", raw.get("entry_price", 0.0))),
        "fee": _safe_float(raw.get("fee", 0.0)),
        "slippage": _safe_float(raw.get("slippage", 0.0)),
        "exit_reason": str(raw.get("exit_reason", "")),
        "leakage_guard": str(raw.get("leakage_guard", "strict_t_plus_1")),
        "rule_version": str(raw.get("rule_version", "")),
        "metadata": dict(raw.get("metadata", {})) if isinstance(raw.get("metadata"), dict) else {},
    }


def _sort_key_fill(item: dict[str, Any]) -> tuple[str, int, str]:
    return (
        str(item.get("fill_ts_utc", "")),
        _safe_int(item.get("order_id", 0)),
        str(item.get("symbol", "")),
    )


def load_lean_trades(run_dir: Path) -> list[dict[str, Any]]:
    return _load_json_list(run_dir / "lean_trades_raw.json")


def load_lean_fills(run_dir: Path) -> list[dict[str, Any]]:
    raw_fills = _load_json_list(run_dir / "lean_fills_raw.json")
    normalized = [_normalize_fill(item) for item in raw_fills]
    return sorted(normalized, key=_sort_key_fill)
