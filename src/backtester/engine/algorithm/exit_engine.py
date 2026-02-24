"""Exit logic evaluation for LEAN algorithm.

Ported from BacktesterV2/execution/exit_engine.py.
Runs inside the LEAN Docker container alongside QM_MVP.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd

from backtester.models import OrderIntent


@dataclass(frozen=True)
class PositionSnapshot:
    experiment_id: str
    symbol: str
    qty_open: int
    entry_avg_price: float
    entry_day: date
    setup_type: str
    initial_stop: float
    dynamic_stop: float
    partial_done: bool
    scan_day: str
    trade_day: str
    trailing_ma_days: int


@dataclass(frozen=True)
class ExitPlan:
    reason: str
    signal_ts_utc: datetime
    qty: int


def _base_exit_intent(
    position: PositionSnapshot,
    signal_ts_utc: datetime,
    qty: int,
    reason: str,
) -> OrderIntent:
    return OrderIntent(
        experiment_id=position.experiment_id,
        symbol=position.symbol,
        side="SELL",
        order_type="MARKET",
        signal_ts_utc=signal_ts_utc,
        created_ts_utc=signal_ts_utc,
        earliest_exec_ts_utc=signal_ts_utc + timedelta(minutes=1),
        qty=max(0, int(qty)),
        metadata={
            "role": "exit",
            "setup_type": position.setup_type,
            "scan_day": position.scan_day,
            "trade_day": position.trade_day,
            "exit_reason": reason,
            "leakage_guard": "strict_t_plus_1",
        },
    )


def evaluate_intraday_stop(position: PositionSnapshot, bar: Any, now_utc: datetime) -> OrderIntent | None:
    if position.qty_open <= 0:
        return None
    low = float(getattr(bar, "Low", getattr(bar, "low", 0.0)))
    if low <= float(position.dynamic_stop):
        return _base_exit_intent(
            position=position,
            signal_ts_utc=now_utc,
            qty=position.qty_open,
            reason="intraday_stop",
        )
    return None


def _is_open_window(now_est: datetime) -> bool:
    return now_est.hour == 9 and now_est.minute <= 35


def _is_close_window(now_est: datetime) -> bool:
    return (now_est.hour == 15 and now_est.minute >= 58) or now_est.hour > 15


def evaluate_partial_exit(position: PositionSnapshot, now_est: datetime, config: Any) -> OrderIntent | None:
    if position.qty_open <= 1:
        return None
    if position.setup_type != "common_breakout":
        return None
    if position.partial_done:
        return None

    partial_day = int(getattr(config, "partial_exit_day", 3))
    partial_fraction = float(getattr(config, "partial_exit_fraction", 0.3333))
    if (now_est.date() - position.entry_day).days < partial_day:
        return None

    partial_exit_time = str(getattr(config, "partial_exit_time", "any_bar"))
    if partial_exit_time == "open" and not _is_open_window(now_est):
        return None
    if partial_exit_time == "close_window" and not _is_close_window(now_est):
        return None

    qty = max(1, int(round(position.qty_open * partial_fraction)))
    if qty >= position.qty_open:
        qty = max(1, position.qty_open - 1)
    signal_ts = now_est.astimezone(timezone.utc) if now_est.tzinfo else now_est.replace(tzinfo=timezone.utc)
    return _base_exit_intent(
        position=position,
        signal_ts_utc=signal_ts,
        qty=qty,
        reason=f"partial_exit_day_{partial_day}",
    )


def evaluate_daily_ma_exit(
    position: PositionSnapshot,
    daily_df: pd.DataFrame,
    scan_day: date,
    *,
    ma_exit_execution: str = "next_open",
    trailing_switch_mode: str = "always",
) -> ExitPlan | None:
    if position.qty_open <= 0:
        return None

    ma_days = max(2, int(position.trailing_ma_days))
    if daily_df.empty or len(daily_df) < ma_days:
        return None

    df = daily_df.copy()
    trading_days = pd.to_datetime(df["trading_day"]).dt.date
    df = df.loc[trading_days <= scan_day].copy()
    if len(df) < ma_days:
        return None

    close = pd.to_numeric(df["close"], errors="coerce")
    if close.isna().iloc[-1]:
        return None
    ma = float(close.tail(ma_days).mean())
    last_close = float(close.iloc[-1])
    if not pd.notna(ma):
        return None

    if trailing_switch_mode == "ma_above_initial_stop":
        if ma <= position.initial_stop:
            return None

    if last_close < ma:
        if ma_exit_execution == "moc":
            signal_ts_utc = datetime.combine(scan_day, datetime.min.time(), tzinfo=timezone.utc).replace(
                hour=20, minute=58
            )
        else:
            signal_ts_utc = datetime.combine(scan_day, datetime.min.time(), tzinfo=timezone.utc).replace(
                hour=23, minute=59
            )
        reason = "daily_ma_exit" if ma_exit_execution == "next_open" else "daily_ma_exit_moc"
        return ExitPlan(reason=reason, signal_ts_utc=signal_ts_utc, qty=position.qty_open)
    return None
