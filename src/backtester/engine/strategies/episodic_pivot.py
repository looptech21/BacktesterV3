"""Episodic Pivot strategy adapter.

Ported from BacktesterV2/tradingsetups/episodic_pivot.py.
Detects gap-up + volume confirmation setups for momentum entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from backtester.engine.strategies.base import SetupAdapter, ensure_daily_history_columns
from backtester.models import DailyScanContext, SetupSignal

RULE_VERSION = "episodic_pivot_v2"


@dataclass(frozen=True)
class EpisodicPivotParams:
    min_gap_pct: float = 10.0
    opening_volume_window_minutes: int = 20
    min_opening_volume_ratio: float = 1.0
    atr_period: int = 14
    adr_period: int = 21
    max_stop_multiple: float = 1.0
    max_stop_multiple_hard: float = 1.5
    max_prior_runup_pct: float | None = None
    entry_ladder_minutes: list[int] | None = None
    stop_cap_mode: str = "hard"
    trailing_ma_days: int = 10
    rule_version: str = ""


class EpisodicPivotAdapter(SetupAdapter):
    """Baseline episodic pivot implementation.

    D-1 based — emits an intent/watch signal to be confirmed on day D
    by intraday gap/volume + ORH logic in execution layer.
    """

    def __init__(self, params: EpisodicPivotParams | None = None) -> None:
        self.params = params or EpisodicPivotParams()

    def compute_setups(self, daily_history_up_to_dminus1: pd.DataFrame, ctx: DailyScanContext) -> list[SetupSignal]:
        ensure_daily_history_columns(daily_history_up_to_dminus1)
        df = daily_history_up_to_dminus1.sort_values("trading_day").copy()
        if len(df) < 30:
            return []
        max_day = pd.to_datetime(df["trading_day"]).dt.date.max()
        if max_day >= ctx.signal_day:
            raise ValueError("daily history contains signal day or future data; expected <= D-1 only")

        symbol = str(df["ticker"].iloc[-1]).upper()
        last = df.iloc[-1]

        avg_volume_20 = float(df["volume"].tail(20).mean()) if len(df) >= 20 else float(df["volume"].mean())
        if avg_volume_20 <= 0:
            return []

        lookback_63 = df["close"].tail(63)
        if len(lookback_63) >= 2:
            runup_pct = (float(lookback_63.iloc[-1]) / float(lookback_63.iloc[0]) - 1.0) * 100.0
            max_prior_runup_pct = self.params.max_prior_runup_pct
            if max_prior_runup_pct is not None and runup_pct > float(max_prior_runup_pct):
                return []
        else:
            runup_pct = 0.0

        signal_ts = datetime.combine(ctx.signal_day, datetime.min.time(), tzinfo=timezone.utc) - timedelta(minutes=1)
        signal = SetupSignal(
            symbol=symbol,
            setup_type="episodic_pivot",
            signal_ts_utc=signal_ts,
            trading_day=ctx.signal_day,
            breakout_level=None,
            stop_level=float(last["low"]),
            validity_end_ts_utc=signal_ts + timedelta(days=1),
            metadata={
                "rule_version": self.params.rule_version or RULE_VERSION,
                "requires_intraday_confirmation": True,
                "confirmation_mode": "rth_gap_proxy_plus_opening_volume",
                "min_gap_pct": self.params.min_gap_pct,
                "opening_volume_window_minutes": self.params.opening_volume_window_minutes,
                "min_opening_volume_ratio": self.params.min_opening_volume_ratio,
                "atr_period": self.params.atr_period,
                "adr_period": self.params.adr_period,
                "max_stop_multiple": self.params.max_stop_multiple,
                "max_stop_multiple_hard": self.params.max_stop_multiple_hard,
                "trailing_ma_days": self.params.trailing_ma_days,
                "runup_63d_pct": runup_pct,
                "max_prior_runup_pct": self.params.max_prior_runup_pct,
                "entry_ladder_minutes": self.params.entry_ladder_minutes or [1, 5, 60],
                "stop_cap_mode": self.params.stop_cap_mode,
            },
        )
        return [signal]
