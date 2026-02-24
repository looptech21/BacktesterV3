"""Position sizing and margin policies.

Ported from BacktesterV2/execution/policies.py.
Runs inside the LEAN Docker container alongside QM_MVP.py.
"""

from __future__ import annotations


def calc_position_size(
    equity: float,
    entry_price: float,
    stop_price: float,
    risk_per_trade: float,
    current_positions: int,
    max_positions: int,
) -> int:
    if current_positions >= max_positions:
        return 0
    if entry_price <= 0:
        return 0
    per_share_risk = entry_price - stop_price
    if per_share_risk <= 0:
        return 0
    dollar_risk = equity * risk_per_trade
    risk_qty = int(dollar_risk // per_share_risk)
    max_affordable_qty = int(equity // entry_price)
    qty = min(risk_qty, max_affordable_qty)
    return max(qty, 0)


def cap_qty_by_margin(
    requested_qty: int,
    entry_price: float,
    leverage: float,
    margin_remaining: float,
    reserved_margin: float,
    margin_buffer_pct: float,
) -> int:
    if requested_qty <= 0:
        return 0
    if entry_price <= 0:
        return 0

    effective_leverage = max(1.0, float(leverage))
    remaining = float(margin_remaining) - max(0.0, float(reserved_margin))
    if remaining <= 0:
        return 0

    clamped_buffer = min(1.0, max(0.0, float(margin_buffer_pct)))
    buffered_margin = remaining * (1.0 - clamped_buffer)
    if buffered_margin <= 0:
        return 0

    per_share_margin = entry_price / effective_leverage
    if per_share_margin <= 0:
        return 0

    margin_qty = int(buffered_margin // per_share_margin)
    return max(0, min(int(requested_qty), margin_qty))
