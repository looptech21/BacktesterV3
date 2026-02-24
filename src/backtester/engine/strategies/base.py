"""SetupAdapter protocol — the contract for strategy implementations.

Every strategy adapter must implement compute_setups() which takes
daily history up to D-1 only (strict no-lookahead).
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from backtester.models import DailyScanContext, SetupSignal


class SetupAdapter(Protocol):
    def compute_setups(self, daily_history_up_to_dminus1: pd.DataFrame, ctx: DailyScanContext) -> list[SetupSignal]:
        """Compute setup signals from daily bars only up to D-1.

        The adapter must not consume any bar from signal day D or later.
        """


def ensure_daily_history_columns(df: pd.DataFrame) -> None:
    required = {"ticker", "trading_day", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"daily_history missing required columns: {sorted(missing)}")
