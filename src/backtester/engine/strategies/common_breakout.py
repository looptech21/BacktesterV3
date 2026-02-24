"""Common Breakout strategy adapter.

Ported from BacktesterV2/tradingsetups/common_breakout.py.
Detects consolidation patterns after impulse moves and emits breakout signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from backtester.engine.strategies.base import SetupAdapter, ensure_daily_history_columns
from backtester.models import DailyScanContext, SetupSignal

RULE_VERSION = "common_breakout_v2"


@dataclass(frozen=True)
class CommonBreakoutParams:
    impulse_lookback_days: int = 63
    min_impulse_return_pct: float = 30.0
    consolidation_min_days: int = 10
    consolidation_max_days: int = 40
    max_consolidation_depth_pct: float = 25.0
    atr_period: int = 14
    adr_period: int = 21
    max_stop_multiple: float = 1.0
    entry_ladder_minutes: list[int] | None = None
    stop_cap_mode: str = "hard"
    rule_version: str = ""


class CommonBreakoutAdapter(SetupAdapter):
    """Baseline common breakout implementation."""

    def __init__(self, params: CommonBreakoutParams | None = None) -> None:
        self.params = params or CommonBreakoutParams()

    def compute_setups(self, daily_history_up_to_dminus1: pd.DataFrame, ctx: DailyScanContext) -> list[SetupSignal]:
        ensure_daily_history_columns(daily_history_up_to_dminus1)
        df = daily_history_up_to_dminus1.sort_values("trading_day").copy()
        if df.empty:
            return []
        max_day = pd.to_datetime(df["trading_day"]).dt.date.max()
        if max_day >= ctx.signal_day:
            raise ValueError("daily history contains signal day or future data; expected <= D-1 only")

        symbol = str(df["ticker"].iloc[-1]).upper()
        p = self.params
        min_len = p.consolidation_max_days + 5
        if len(df) < min_len:
            return []

        found_signal: SetupSignal | None = None
        for cons_len in range(p.consolidation_min_days, p.consolidation_max_days + 1):
            if len(df) <= cons_len + 1:
                continue

            consolidation = df.iloc[-cons_len:]
            pre = df.iloc[:-cons_len]
            if len(pre) < 5:
                continue

            cons_high = float(consolidation["high"].max())
            cons_low = float(consolidation["low"].min())
            if cons_high <= 0:
                continue

            depth_pct = ((cons_high - cons_low) / cons_high) * 100.0
            if depth_pct > p.max_consolidation_depth_pct:
                continue

            impulse_window = pre.tail(p.impulse_lookback_days)
            if impulse_window.empty:
                continue

            impulse_low = float(impulse_window["low"].min())
            if impulse_low <= 0:
                continue

            cons_start_close = float(consolidation["close"].iloc[0])
            impulse_return_pct = (cons_start_close / impulse_low - 1.0) * 100.0
            if impulse_return_pct < p.min_impulse_return_pct:
                continue

            last_day = df.iloc[-1]
            signal_ts = datetime.combine(ctx.signal_day, datetime.min.time(), tzinfo=timezone.utc) - timedelta(
                minutes=1
            )
            found_signal = SetupSignal(
                symbol=symbol,
                setup_type="common_breakout",
                signal_ts_utc=signal_ts,
                trading_day=ctx.signal_day,
                breakout_level=cons_high,
                stop_level=float(last_day["low"]),
                validity_end_ts_utc=signal_ts + timedelta(days=1),
                metadata={
                    "rule_version": self.params.rule_version or RULE_VERSION,
                    "cons_len": cons_len,
                    "depth_pct": depth_pct,
                    "impulse_return_pct": impulse_return_pct,
                    "atr_period": p.atr_period,
                    "adr_period": p.adr_period,
                    "max_stop_multiple": p.max_stop_multiple,
                    "entry_ladder_minutes": p.entry_ladder_minutes or [1, 5, 60],
                    "stop_cap_mode": p.stop_cap_mode,
                },
            )
            break

        return [found_signal] if found_signal else []
