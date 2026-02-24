"""FIFO trade matching — entry fills matched to exit fills.

Ported from BacktesterV2/reporting/trade_matcher.py.
Handles multi-fill round-trips, lot tracking, and violation reporting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class MatchingResult:
    trades: list[dict[str, Any]]
    violations: list[dict[str, Any]]
    num_open_lots_end: int
    num_fills: int
    num_closed_trades: int


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _sort_key_fill(item: dict[str, Any]) -> tuple[str, int, str]:
    return (
        str(item.get("fill_ts_utc", "")),
        int(item.get("order_id", 0)),
        str(item.get("symbol", "")),
    )


def match_trades_fifo(
    fills: list[dict[str, Any]],
    config: Any | None = None,
) -> MatchingResult:
    sorted_fills = sorted(fills, key=_sort_key_fill)
    open_lots: dict[str, list[dict[str, Any]]] = {}
    trades: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    trade_seq = 1

    for fill in sorted_fills:
        symbol = str(fill.get("symbol", "")).upper()
        if not symbol:
            violations.append({"type": "fill_schema", "reason": "missing_symbol", "fill": fill})
            continue

        role = str(fill.get("role", "")).lower()
        side = str(fill.get("side", "")).upper()
        qty = int(fill.get("qty", 0) or 0)
        price = float(fill.get("fill_price", 0.0) or 0.0)
        fee = float(fill.get("fee", 0.0) or 0.0)
        if qty <= 0:
            violations.append({"type": "fill_schema", "reason": "non_positive_qty", "fill": fill})
            continue

        if role == "entry" and side == "BUY":
            open_lots.setdefault(symbol, []).append(
                {
                    "symbol": symbol,
                    "setup_type": str(fill.get("setup_type", "unknown")),
                    "setup_lot_id": str(fill.get("setup_lot_id", "")),
                    "entry_stage": int(fill.get("entry_stage", 0) or 0),
                    "stop_mode": str(fill.get("stop_mode", "")),
                    "entry_signal_ts_utc": str(fill.get("signal_ts_utc", "")),
                    "entry_fill_ts_utc": str(fill.get("fill_ts_utc", "")),
                    "entry_price": price,
                    "remaining_qty": qty,
                    "entry_fee": fee,
                    "scan_day": str(fill.get("scan_day", "")),
                    "trade_day": str(fill.get("trade_day", "")),
                    "experiment_id": str(fill.get("experiment_id", "")),
                    "metadata": {"entry_order_id": int(fill.get("order_id", 0))},
                }
            )
            continue

        if role != "exit" or side != "SELL":
            violations.append({
                "type": "fill_schema",
                "reason": "unexpected_role_or_side",
                "symbol": symbol,
                "role": role,
                "side": side,
                "fill": fill,
            })
            continue

        remaining_exit = qty
        symbol_lots = open_lots.setdefault(symbol, [])
        target_lot_id = str(fill.get("setup_lot_id", "") or "")
        if not symbol_lots:
            violations.append({
                "type": "position_underflow",
                "reason": "exit_without_open_lot",
                "symbol": symbol,
                "exit_qty": qty,
                "fill": fill,
            })
            continue

        while remaining_exit > 0 and symbol_lots:
            lot_idx = 0
            if target_lot_id:
                lot_idx = -1
                for idx, candidate in enumerate(symbol_lots):
                    if (
                        str(candidate.get("setup_lot_id", "")) == target_lot_id
                        and int(candidate.get("remaining_qty", 0)) > 0
                    ):
                        lot_idx = idx
                        break
                if lot_idx < 0:
                    violations.append({
                        "type": "position_underflow",
                        "reason": "exit_lot_id_not_found",
                        "symbol": symbol,
                        "setup_lot_id": target_lot_id,
                        "remaining_unmatched_qty": remaining_exit,
                        "fill": fill,
                    })
                    break

            lot = symbol_lots[lot_idx]
            matched = min(remaining_exit, int(lot["remaining_qty"]))
            if matched <= 0:
                symbol_lots.pop(lot_idx)
                continue

            remaining_exit -= matched
            lot["remaining_qty"] = int(lot["remaining_qty"]) - matched

            entry_fee_alloc = float(lot.get("entry_fee", 0.0)) * (matched / max(1, matched + lot["remaining_qty"]))
            exit_fee_alloc = fee * (matched / qty)
            pnl_gross = (price - float(lot["entry_price"])) * matched
            pnl_net = pnl_gross - entry_fee_alloc - exit_fee_alloc

            entry_fill_dt = _parse_dt(lot["entry_fill_ts_utc"])
            exit_fill_dt = _parse_dt(fill.get("fill_ts_utc"))
            holding_minutes = None
            if entry_fill_dt and exit_fill_dt:
                holding_minutes = max(0.0, (exit_fill_dt - entry_fill_dt).total_seconds() / 60.0)
            return_pct = 0.0
            if float(lot["entry_price"]) > 0:
                return_pct = (price / float(lot["entry_price"])) - 1.0

            trade = {
                "trade_id": f"T{trade_seq:08d}",
                "experiment_id": lot["experiment_id"],
                "symbol": symbol,
                "setup_type": lot["setup_type"],
                "setup_lot_id": lot.get("setup_lot_id", ""),
                "entry_stage": int(lot.get("entry_stage", 0) or 0),
                "stop_mode": lot.get("stop_mode", ""),
                "entry_signal_ts_utc": lot["entry_signal_ts_utc"],
                "entry_fill_ts_utc": lot["entry_fill_ts_utc"],
                "entry_price": float(lot["entry_price"]),
                "entry_qty": matched,
                "exit_fill_ts_utc": str(fill.get("fill_ts_utc", "")),
                "exit_price": price,
                "exit_qty": matched,
                "qty": matched,
                "holding_minutes": holding_minutes,
                "pnl_gross": pnl_gross,
                "pnl_net": pnl_net,
                "pnl": pnl_net,
                "return_pct": return_pct,
                "exit_reason": str(fill.get("exit_reason", "unknown")),
                "scan_day": lot["scan_day"],
                "trade_day": lot["trade_day"],
                "entry_order_id": int(lot["metadata"]["entry_order_id"]),
                "exit_order_id": int(fill.get("order_id", 0)),
            }
            trades.append(trade)
            trade_seq += 1

            if lot["remaining_qty"] <= 0:
                symbol_lots.pop(lot_idx)

        if remaining_exit > 0:
            violations.append({
                "type": "position_underflow",
                "reason": "exit_larger_than_open_lots",
                "symbol": symbol,
                "remaining_unmatched_qty": remaining_exit,
                "fill": fill,
            })

    num_open = sum(sum(int(lot["remaining_qty"]) for lot in lots) for lots in open_lots.values())
    return MatchingResult(
        trades=trades,
        violations=violations,
        num_open_lots_end=num_open,
        num_fills=len(sorted_fills),
        num_closed_trades=len(trades),
    )
