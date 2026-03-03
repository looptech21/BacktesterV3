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
    min_impulse_return_pct: float = 50.0
    min_3m_return_pct: float | None = 40.0
    consolidation_min_days: int = 10
    consolidation_max_days: int = 40
    max_consolidation_depth_pct: float = 25.0
    require_orderly_lows: bool = True
    consolidation_low_tolerance_pct: float = 2.0
    require_tightening: bool = True
    require_ma_alignment: bool = True
    ma_alignment_periods: list[int] | None = None
    ma_alignment_tolerance_pct: float = 3.0
    require_volume_contraction: bool = True
    max_consolidation_volume_ratio: float = 0.7
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

            # Orderly lows check: 2nd-half low must be within tolerance of 1st-half low
            if p.require_orderly_lows and cons_len >= 4:
                mid = cons_len // 2
                first_half_low = float(consolidation.iloc[:mid]["low"].min())
                second_half_low = float(consolidation.iloc[mid:]["low"].min())
                threshold = first_half_low * (1.0 - p.consolidation_low_tolerance_pct / 100.0)
                if second_half_low < threshold:
                    continue

            # Tightening check: 2nd-half range must be narrower than 1st-half range
            if p.require_tightening and cons_len >= 4:
                mid = cons_len // 2
                first_half = consolidation.iloc[:mid]
                second_half = consolidation.iloc[mid:]
                first_range = float(first_half["high"].max()) - float(first_half["low"].min())
                second_range = float(second_half["high"].max()) - float(second_half["low"].min())
                if second_range >= first_range:
                    continue

            # MA alignment: last close must be within tolerance of each specified MA
            if p.require_ma_alignment:
                ma_periods = p.ma_alignment_periods or [20, 50]
                last_close = float(df["close"].iloc[-1])
                ma_ok = True
                for ma_len in ma_periods:
                    if len(df) < ma_len:
                        ma_ok = False
                        break
                    ma_val = float(df["close"].tail(ma_len).mean())
                    min_allowed = ma_val * (1.0 - p.ma_alignment_tolerance_pct / 100.0)
                    if last_close < min_allowed:
                        ma_ok = False
                        break
                if not ma_ok:
                    continue

            # Volume contraction: consolidation avg volume must be lower than impulse avg volume
            if p.require_volume_contraction:
                cons_avg_vol = float(consolidation["volume"].mean())
                impulse_vol_window = pre.tail(p.impulse_lookback_days)
                if not impulse_vol_window.empty:
                    impulse_avg_vol = float(impulse_vol_window["volume"].mean())
                    if impulse_avg_vol > 0 and cons_avg_vol / impulse_avg_vol > p.max_consolidation_volume_ratio:
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

            # 3-month return check: current price vs 63 trading days ago
            if p.min_3m_return_pct is not None and len(df) >= 63:
                close_now = float(df["close"].iloc[-1])
                close_63d_ago = float(df["close"].iloc[-63])
                if close_63d_ago > 0:
                    return_3m = (close_now / close_63d_ago - 1.0) * 100.0
                    if return_3m < p.min_3m_return_pct:
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
