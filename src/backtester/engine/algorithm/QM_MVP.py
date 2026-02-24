"""LEAN Python algorithm orchestrator for BacktesterV3.

Ported from BacktesterV2/algorithms/QM_MVP.py with V3 import paths.

This module intentionally keeps strategy rules configurable and modular:
- Daily scan uses data up to D-1 only.
- Intraday execution enforces strict t+1 order queue semantics.
- Setup details are delegated to adapters in `engine/strategies/`.
"""

from __future__ import annotations

import json
import sys
import builtins
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from backtester.engine.algorithm.exit_engine import (
    PositionSnapshot,
    evaluate_daily_ma_exit,
    evaluate_intraday_stop,
    evaluate_partial_exit,
)
from backtester.engine.algorithm.order_queue import OrderQueue
from backtester.engine.algorithm.policies import calc_position_size, cap_qty_by_margin
from backtester.config import RunConfig
from backtester.models import DailyScanContext, OrderIntent, SetupIntent
from backtester.engine.strategies.common_breakout import CommonBreakoutAdapter, CommonBreakoutParams
from backtester.engine.strategies.episodic_pivot import EpisodicPivotAdapter, EpisodicPivotParams

ORDER_TAG_PREFIX = "b3v1|"

try:
    from AlgorithmImports import *  # type: ignore  # noqa: F403
except ModuleNotFoundError:  # pragma: no cover
    for lean_path in ("/Lean/Launcher/bin/Debug", "/Lean/Launcher/bin/Release"):
        if Path(lean_path, "AlgorithmImports.py").exists() and lean_path not in sys.path:
            sys.path.insert(0, lean_path)

    try:
        from AlgorithmImports import *  # type: ignore  # noqa: F403
    except ModuleNotFoundError:
        class QCAlgorithm:  # type: ignore
            pass

        class Resolution:  # type: ignore
            Minute = "Minute"


@dataclass
class IntradayState:
    day: date
    orh_window_minutes: int
    open_range_high: float | None = None
    running_day_low: float | None = None
    open_range_end_est: datetime | None = None
    orh_ready: bool = False


@dataclass
class PositionState:
    setup_lot_id: str
    symbol: str
    qty_open: int
    entry_avg_price: float
    entry_day: date
    setup_type: str
    entry_stage: int
    stop_mode: str
    initial_stop: float
    dynamic_stop: float
    partial_done: bool
    scan_day: str
    trade_day: str
    trailing_ma_days: int
    last_entry_signal_ts_utc: str
    last_exit_signal_day: date | None = None


def _ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except builtins.Exception:
        return None


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except builtins.Exception:
        return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except builtins.Exception:
        return default


def build_trade_records_from_fills(fill_events: list[dict[str, Any]], experiment_id: str) -> list[dict[str, Any]]:
    fills_sorted = sorted(
        fill_events,
        key=lambda item: (
            str(item.get("fill_ts_utc", "")),
            int(item.get("order_id", 0)),
            str(item.get("symbol", "")),
        ),
    )

    trades: list[dict[str, Any]] = []
    seq = 1
    for fill in fills_sorted:
        if str(fill.get("role", "")).lower() != "entry":
            continue
        signal_ts = str(fill.get("signal_ts_utc", ""))
        fill_ts = str(fill.get("fill_ts_utc", ""))
        signal_dt = _ts(signal_ts)
        fill_dt = _ts(fill_ts)
        leakage_guard_pass = bool(signal_dt and fill_dt and fill_dt > signal_dt)

        trades.append(
            {
                "trade_id": f"E{seq:08d}",
                "experiment_id": experiment_id,
                "order_id": int(fill.get("order_id", 0)),
                "symbol": str(fill.get("symbol", "")),
                "setup_type": str(fill.get("setup_type", "unknown")),
                "setup_lot_id": str(fill.get("setup_lot_id", "")),
                "entry_stage": int(fill.get("entry_stage", 0) or 0),
                "stop_mode": str(fill.get("stop_mode", "")),
                "entry_qty": int(fill.get("qty", 0)),
                "entry_signal_ts_utc": signal_ts,
                "entry_fill_ts_utc": fill_ts,
                "entry_price": _safe_float(fill.get("fill_price", 0.0)),
                "exit_fill_ts_utc": None,
                "exit_price": None,
                "exit_qty": None,
                "pnl": 0.0,
                "pnl_net": 0.0,
                "scan_day": str(fill.get("scan_day", "")),
                "trade_day": str(fill.get("trade_day", "")),
                "leakage_guard": "strict_t_plus_1",
                "leakage_guard_pass": leakage_guard_pass,
            }
        )
        seq += 1
    return trades


class QM_MVP(QCAlgorithm):
    def Initialize(self) -> None:  # noqa: N802
        config_json = self.GetParameter("config-json") if hasattr(self, "GetParameter") else ""
        if config_json:
            self.run_config = RunConfig.model_validate(json.loads(config_json))
        else:
            raise RuntimeError("config-json parameter is required")

        tickers_param = self.GetParameter("tickers") if hasattr(self, "GetParameter") else ""
        self.tickers = [t.strip() for t in tickers_param.split(",") if t.strip()] if tickers_param else []

        self.runtime_experiment_id = (
            self.GetParameter("experiment-id") if hasattr(self, "GetParameter") else ""
        ) or "lean_runtime"

        self.SetStartDate(
            self.run_config.period.start_date.year,
            self.run_config.period.start_date.month,
            self.run_config.period.start_date.day,
        )
        self.SetEndDate(
            self.run_config.period.end_date.year,
            self.run_config.period.end_date.month,
            self.run_config.period.end_date.day,
        )
        self.SetCash(self.run_config.execution.initial_cash)

        self.symbol_map: dict[str, Any] = {}
        for ticker in self.tickers:
            security = self.AddEquity(ticker, Resolution.Minute)
            self.symbol_map[ticker] = security.Symbol

        cb_raw = self.run_config.setups.common_breakout.model_dump()
        cb_kwargs = {k: cb_raw[k] for k in CommonBreakoutParams.__dataclass_fields__.keys() if k in cb_raw}
        self.common_adapter = CommonBreakoutAdapter(CommonBreakoutParams(**cb_kwargs))

        ep_raw = self.run_config.setups.episodic_pivot.model_dump()
        ep_kwargs = {k: ep_raw[k] for k in EpisodicPivotParams.__dataclass_fields__.keys() if k in ep_raw}
        self.ep_adapter = EpisodicPivotAdapter(EpisodicPivotParams(**ep_kwargs))

        self.daily_history: dict[str, pd.DataFrame] = {
            t: pd.DataFrame(columns=["ticker", "trading_day", "open", "high", "low", "close", "volume"])
            for t in self.tickers
        }
        self.pending_intents_by_day: dict[date, list[SetupIntent]] = {}
        self.intraday_state: dict[str, IntradayState] = {}
        self.pending_symbols: set[str] = set()

        self.positions_by_lot: dict[str, PositionState] = {}
        self.pending_exit_qty_by_lot: dict[str, int] = {}
        self.submitted_lot_ids: set[str] = set()
        self.blocked_lot_ids_today: set[str] = set()
        self.opening_context_by_ticker: dict[str, dict[str, Any]] = {}
        self.setup_trace_events: list[dict[str, Any]] = []

        self.order_queue = OrderQueue()
        self.order_meta: dict[int, dict[str, Any]] = {}
        self.fill_events: list[dict[str, Any]] = []
        self.current_day: date | None = None

        self.runtime_debug: dict[str, Any] = {
            "daily_bars": 0,
            "setups_generated": 0,
            "intents_created": 0,
            "order_intents_enqueued": 0,
            "orders_submitted": 0,
            "entry_orders_enqueued": 0,
            "exit_orders_enqueued": 0,
            "entry_fills": 0,
            "exit_fills": 0,
            "ma_exit_signals": 0,
            "intraday_stop_signals": 0,
            "partial_exit_signals": 0,
            "forced_flatten_signals": 0,
            "position_underflow_blocked": 0,
            "entry_qty_requested_total": 0,
            "entry_qty_submitted_total": 0,
            "entry_qty_downsized_events": 0,
            "entry_blocked_no_margin": 0,
            "entry_blocked_stop_data_missing": 0,
            "entry_blocked_stop_cap": 0,
            "entry_warn_stop_cap": 0,
            "entry_blocked_ep_gate": 0,
            "config_common_breakout_enabled": int(self.run_config.setups.common_breakout.enabled),
            "config_episodic_pivot_enabled": int(self.run_config.setups.episodic_pivot.enabled),
        }

        for ticker, symbol in self.symbol_map.items():
            self.Consolidate(symbol, Resolution.Daily, self._make_daily_handler(ticker))

    def _make_daily_handler(self, ticker: str):
        def _handler(*args: Any) -> None:
            bar = args[-1] if args else None
            if bar is None:
                return
            self._on_daily_bar(ticker, bar)

        return _handler

    def _next_trading_day(self, day: date) -> date:
        probe = day + timedelta(days=1)
        try:
            sample_symbol = next(iter(self.symbol_map.values()))
            exchange_hours = self.Securities[sample_symbol].Exchange.Hours
            while not exchange_hours.IsDateOpen(probe):
                probe += timedelta(days=1)
        except builtins.Exception:
            pass
        return probe

    @staticmethod
    def _normalize_stop_mode(mode: str) -> str:
        raw = str(mode or "running_lod").strip().lower()
        if raw == "running_lod":
            return "running_lod_at_entry"
        return raw

    def _entry_ladder_for_setup(self, setup_type: str) -> list[int]:
        if setup_type == "common_breakout":
            ladder = list(getattr(self.run_config.setups.common_breakout, "entry_ladder_minutes", [1, 5, 60]))
            fallback = int(self.run_config.setups.common_breakout.orh_window_minutes)
        else:
            ladder = list(getattr(self.run_config.setups.episodic_pivot, "entry_ladder_minutes", [1, 5, 60]))
            fallback = int(self.run_config.setups.episodic_pivot.orh_window_minutes)
        out: list[int] = []
        for item in ladder:
            minute = int(item)
            if minute > 0 and minute not in out:
                out.append(minute)
        if not out:
            out = [max(1, fallback)]
        return out

    def _stop_mode_for_setup(self, setup_type: str) -> str:
        if setup_type == "common_breakout":
            mode = getattr(self.run_config.setups.common_breakout, "stop_mode", "running_lod")
        else:
            mode = getattr(self.run_config.setups.episodic_pivot, "stop_mode", "running_lod")
        return self._normalize_stop_mode(str(mode))

    def _stop_cap_mode_for_setup(self, setup_type: str) -> str:
        if setup_type == "common_breakout":
            value = getattr(self.run_config.setups.common_breakout, "stop_cap_mode", "hard")
        else:
            value = getattr(self.run_config.setups.episodic_pivot, "stop_cap_mode", "hard")
        mode = str(value or "hard").strip().lower()
        if mode not in {"hard", "warn", "off"}:
            return "hard"
        return mode

    def _cap_params_for_setup(self, setup_type: str) -> tuple[int, int, float]:
        if setup_type == "common_breakout":
            cfg = self.run_config.setups.common_breakout
        else:
            cfg = self.run_config.setups.episodic_pivot
        return int(cfg.atr_period), int(cfg.adr_period), float(cfg.max_stop_multiple)

    def _trace_setup_event(self, event_type: str, **payload: Any) -> None:
        event = {
            "event_type": event_type,
            "event_ts_utc": str(getattr(self, "UtcTime", datetime.now(timezone.utc))),
            "event_ts_est": str(getattr(self, "Time", datetime.now(timezone.utc))),
        }
        event.update(payload)
        self.setup_trace_events.append(event)

    def _lots_for_symbol(self, ticker: str) -> list[tuple[str, PositionState]]:
        out: list[tuple[str, PositionState]] = []
        for lot_id, pos in self.positions_by_lot.items():
            if pos.symbol == ticker and pos.qty_open > 0:
                out.append((lot_id, pos))
        return out

    def _get_previous_day_row(self, ticker: str, today: date) -> pd.Series | None:
        hist = self.daily_history.get(ticker)
        if hist is None or hist.empty:
            return None
        df = hist.copy()
        days = pd.to_datetime(df["trading_day"]).dt.date
        prev = df.loc[days < today]
        if prev.empty:
            return None
        return prev.iloc[-1]

    def _daily_history_before(self, ticker: str, today: date) -> pd.DataFrame:
        hist = self.daily_history.get(ticker)
        if hist is None or hist.empty:
            return pd.DataFrame(columns=["trading_day", "open", "high", "low", "close", "volume"])
        df = hist.sort_values("trading_day").copy()
        days = pd.to_datetime(df["trading_day"]).dt.date
        return df.loc[days < today].copy()

    def _universe_filter_settings(self) -> dict[str, Any]:
        filters = getattr(self.run_config.universe, "filters", None)
        if hasattr(filters, "model_dump"):
            return filters.model_dump(mode="python")
        return filters if isinstance(filters, dict) else {}

    def _evaluate_universe_filters(
        self,
        *,
        ticker: str,
        day: date,
        signal_day: date,
        hist: pd.DataFrame,
        close_value: float,
    ) -> tuple[bool, str | None, dict[str, Any]]:
        filters = self._universe_filter_settings()
        if not filters:
            return True, None, {}

        min_price = filters.get("min_price")
        max_price = filters.get("max_price")
        min_adv = filters.get("min_avg_dollar_vol_20")

        evidence: dict[str, Any] = {
            "symbol": ticker,
            "scan_day": str(day),
            "trade_day": str(signal_day),
            "close": close_value,
        }

        if min_price not in (None, ""):
            min_price_v = _safe_float(min_price, 0.0)
            evidence["min_price"] = min_price_v
            if close_value < min_price_v:
                return False, "UNIVERSE_MIN_PRICE_UNDER", evidence

        if max_price not in (None, ""):
            max_price_v = _safe_float(max_price, 0.0)
            evidence["max_price"] = max_price_v
            if close_value > max_price_v:
                return False, "UNIVERSE_MAX_PRICE_OVER", evidence

        if min_adv not in (None, ""):
            min_adv_v = _safe_float(min_adv, 0.0)
            evidence["min_avg_dollar_vol_20"] = min_adv_v
            tail = hist.tail(20) if not hist.empty else pd.DataFrame()
            if tail.empty:
                return False, "UNIVERSE_ADV20_MISSING", evidence
            close_series = pd.to_numeric(tail.get("close"), errors="coerce")
            vol_series = pd.to_numeric(tail.get("volume"), errors="coerce")
            adv20 = float((close_series * vol_series).dropna().mean()) if not close_series.empty else 0.0
            evidence["adv20_dollar_vol"] = adv20
            if adv20 < min_adv_v:
                return False, "UNIVERSE_ADV20_UNDER_MIN", evidence

        if bool(filters.get("exclude_otc", True)):
            evidence["exclude_otc"] = True
            evidence["exclude_otc_enforced"] = False

        return True, None, evidence

    def _compute_adr_dollar(self, ticker: str, today: date, period: int) -> float | None:
        hist = self._daily_history_before(ticker, today)
        if hist.empty:
            return None
        width = pd.to_numeric(hist["high"], errors="coerce") - pd.to_numeric(hist["low"], errors="coerce")
        tail = width.tail(max(1, int(period)))
        if tail.empty:
            return None
        value = float(tail.mean())
        return value if value > 0 else None

    def _compute_atr_dollar(self, ticker: str, today: date, period: int) -> float | None:
        hist = self._daily_history_before(ticker, today)
        if len(hist) < 2:
            return None
        highs = pd.to_numeric(hist["high"], errors="coerce")
        lows = pd.to_numeric(hist["low"], errors="coerce")
        closes = pd.to_numeric(hist["close"], errors="coerce")
        trs: list[float] = []
        prev_close: float | None = None
        for high, low, close in zip(highs, lows, closes):
            if not pd.notna(high) or not pd.notna(low) or not pd.notna(close):
                prev_close = float(close) if pd.notna(close) else prev_close
                continue
            high_f = float(high)
            low_f = float(low)
            if prev_close is None:
                tr = high_f - low_f
            else:
                tr = max(high_f - low_f, abs(high_f - prev_close), abs(low_f - prev_close))
            if tr > 0:
                trs.append(tr)
            prev_close = float(close)
        if not trs:
            return None
        window = trs[-max(1, int(period)) :]
        value = float(sum(window) / len(window))
        return value if value > 0 else None

    def _adv20_volume(self, ticker: str, today: date) -> float | None:
        hist = self._daily_history_before(ticker, today)
        if hist.empty:
            return None
        vol = pd.to_numeric(hist["volume"], errors="coerce").tail(20)
        if vol.empty:
            return None
        value = float(vol.mean())
        return value if value > 0 else None

    def _update_opening_context(self, ticker: str, bar: Any, now_est: datetime) -> dict[str, Any]:
        today = now_est.date()
        ctx = self.opening_context_by_ticker.get(ticker)
        if not ctx or ctx.get("day") != today:
            session_open = now_est.replace(hour=9, minute=30, second=0, microsecond=0)
            ctx = {
                "day": today,
                "session_open": session_open,
                "open_price": None,
                "minute_volume": {},
            }
            self.opening_context_by_ticker[ticker] = ctx

        session_open = ctx["session_open"]
        if now_est >= session_open and ctx.get("open_price") is None:
            ctx["open_price"] = float(getattr(bar, "Open", getattr(bar, "open", 0.0)) or 0.0)

        minute_index = int((now_est - session_open).total_seconds() // 60)
        if 0 <= minute_index <= 240:
            minute_volume = ctx.setdefault("minute_volume", {})
            if minute_index not in minute_volume:
                minute_volume[minute_index] = float(getattr(bar, "Volume", getattr(bar, "volume", 0.0)) or 0.0)
        return ctx

    @staticmethod
    def _opening_volume_first_n(ctx: dict[str, Any], minutes: int) -> float:
        minute_volume = ctx.get("minute_volume", {})
        total = 0.0
        for idx in range(max(0, int(minutes))):
            total += float(minute_volume.get(idx, 0.0))
        return total

    def _evaluate_ep_activation(
        self,
        *,
        ticker: str,
        intent: SetupIntent,
        now_est: datetime,
        bar: Any,
    ) -> dict[str, Any]:
        if intent.setup_type != "episodic_pivot":
            return {"status": "pass"}

        prev = self._get_previous_day_row(ticker, now_est.date())
        if prev is None:
            return {"status": "fail", "code": "EP_PREV_DAY_MISSING", "message": "Missing D-1 bar"}

        prev_close = _safe_float(prev.get("close"), 0.0) if hasattr(prev, "get") else _safe_float(prev["close"], 0.0)
        if prev_close <= 0:
            return {"status": "fail", "code": "EP_PREV_CLOSE_INVALID", "message": "Invalid D-1 close"}

        ctx = self._update_opening_context(ticker, bar, now_est)
        open_price = _safe_float(ctx.get("open_price"), 0.0)
        if open_price <= 0:
            return {"status": "pending"}

        min_gap_pct = _safe_float(intent.metadata.get("min_gap_pct", 0.0), 0.0)
        gap_pct = (open_price / prev_close - 1.0) * 100.0
        if gap_pct < min_gap_pct:
            return {
                "status": "fail",
                "code": "EP_GAP_UNDER_MIN",
                "message": "Gap below min_gap_pct",
                "evidence": {"gap_pct": gap_pct, "min_gap_pct": min_gap_pct},
            }

        opening_minutes = max(1, int(intent.metadata.get("opening_volume_window_minutes", 20) or 20))
        session_open = ctx["session_open"]
        if now_est < session_open + timedelta(minutes=opening_minutes):
            return {"status": "pending"}

        adv20 = self._adv20_volume(ticker, now_est.date())
        if adv20 is None or adv20 <= 0:
            return {"status": "fail", "code": "EP_ADV20_MISSING", "message": "ADV20 missing/invalid"}
        opening_volume = self._opening_volume_first_n(ctx, opening_minutes)
        ratio = opening_volume / adv20 if adv20 > 0 else 0.0
        min_ratio = _safe_float(intent.metadata.get("min_opening_volume_ratio", 0.0), 0.0)
        if ratio < min_ratio:
            return {
                "status": "fail",
                "code": "EP_OPENING_VOLUME_RATIO_UNDER_MIN",
                "message": "Opening volume ratio below min threshold",
                "evidence": {
                    "opening_volume_ratio": ratio,
                    "min_opening_volume_ratio": min_ratio,
                    "opening_volume_window_minutes": opening_minutes,
                },
            }
        return {
            "status": "pass",
            "evidence": {
                "gap_pct": gap_pct,
                "min_gap_pct": min_gap_pct,
                "opening_volume_ratio": ratio,
                "min_opening_volume_ratio": min_ratio,
                "opening_volume_window_minutes": opening_minutes,
            },
        }

    def _resolve_stop_price_strict(
        self,
        *,
        ticker: str,
        today: date,
        bar: Any,
        intraday_state: IntradayState,
        stop_mode: str,
    ) -> tuple[float | None, str]:
        if stop_mode == "running_lod_at_entry":
            low = intraday_state.running_day_low
            if low is None:
                low = float(getattr(bar, "Low", getattr(bar, "low", 0.0)) or 0.0)
            if low > 0:
                return float(low), "ok"
            return None, "STOP_RUNNING_LOD_MISSING"

        prev = self._get_previous_day_row(ticker, today)
        if prev is None:
            return None, "STOP_DMINUS1_BAR_MISSING"

        if stop_mode == "dminus1_open":
            value = _safe_float(prev.get("open"), 0.0) if hasattr(prev, "get") else _safe_float(prev["open"], 0.0)
            return (float(value), "ok") if value > 0 else (None, "STOP_DMINUS1_OPEN_INVALID")

        if stop_mode == "dminus1_low":
            value = _safe_float(prev.get("low"), 0.0) if hasattr(prev, "get") else _safe_float(prev["low"], 0.0)
            return (float(value), "ok") if value > 0 else (None, "STOP_DMINUS1_LOW_INVALID")

        return None, "STOP_MODE_UNSUPPORTED"

    def _evaluate_stop_cap(
        self,
        *,
        ticker: str,
        today: date,
        setup_type: str,
        stop_cap_mode: str,
        entry_price: float,
        stop_price: float,
    ) -> dict[str, Any]:
        if stop_cap_mode == "off":
            return {"decision": "pass"}

        atr_period, adr_period, max_stop_multiple = self._cap_params_for_setup(setup_type)
        atr_value = self._compute_atr_dollar(ticker, today, atr_period)
        adr_value = self._compute_adr_dollar(ticker, today, adr_period)
        if atr_value is None or adr_value is None:
            if stop_cap_mode == "hard":
                return {
                    "decision": "block",
                    "code": "STOP_CAP_REFERENCE_MISSING",
                    "evidence": {"atr": atr_value, "adr": adr_value},
                }
            return {
                "decision": "warn",
                "code": "STOP_CAP_REFERENCE_MISSING_WARN",
                "evidence": {"atr": atr_value, "adr": adr_value},
            }

        risk_per_share = float(entry_price) - float(stop_price)
        if risk_per_share <= 0:
            return {
                "decision": "block",
                "code": "STOP_NON_POSITIVE_RISK",
                "evidence": {"entry_price": entry_price, "stop_price": stop_price},
            }
        cap_ref = min(float(atr_value), float(adr_value))
        cap_limit = cap_ref * float(max_stop_multiple)
        if risk_per_share > cap_limit:
            evidence = {
                "risk_per_share": risk_per_share,
                "cap_ref": cap_ref,
                "cap_limit": cap_limit,
                "atr": atr_value,
                "adr": adr_value,
                "max_stop_multiple": max_stop_multiple,
            }
            if stop_cap_mode == "hard":
                return {"decision": "block", "code": "STOP_CAP_BREACH", "evidence": evidence}
            return {"decision": "warn", "code": "STOP_CAP_BREACH_WARN", "evidence": evidence}
        return {"decision": "pass"}

    def _trailing_ma_days_for_setup(self, setup_type: str) -> int:
        if setup_type == "common_breakout":
            return int(self.run_config.setups.common_breakout.trailing_ma_days)
        return int(self.run_config.setups.episodic_pivot.trailing_ma_days)

    def _ma_exit_execution_for_setup(self, setup_type: str) -> str:
        if setup_type == "common_breakout":
            return str(self.run_config.setups.common_breakout.ma_exit_execution)
        return str(self.run_config.setups.episodic_pivot.ma_exit_execution)

    def _trailing_switch_mode_for_setup(self, setup_type: str) -> str:
        if setup_type == "episodic_pivot":
            return str(self.run_config.setups.episodic_pivot.trailing_switch_mode)
        return "always"

    def _position_snapshot(self, position: PositionState) -> PositionSnapshot:
        return PositionSnapshot(
            experiment_id=self.runtime_experiment_id,
            symbol=position.symbol,
            qty_open=position.qty_open,
            entry_avg_price=position.entry_avg_price,
            entry_day=position.entry_day,
            setup_type=position.setup_type,
            initial_stop=position.initial_stop,
            dynamic_stop=position.dynamic_stop,
            partial_done=position.partial_done,
            scan_day=position.scan_day,
            trade_day=position.trade_day,
            trailing_ma_days=position.trailing_ma_days,
        )

    def _queue_exit_intent(self, intent: OrderIntent) -> bool:
        lot_id = str(intent.metadata.get("setup_lot_id", ""))
        if not lot_id:
            return False
        position = self.positions_by_lot.get(lot_id)
        if not position or position.qty_open <= 0:
            return False
        reserved = self.pending_exit_qty_by_lot.get(lot_id, 0)
        available = max(0, position.qty_open - reserved)
        if available <= 0:
            return False
        qty = min(int(intent.qty), available)
        if qty <= 0:
            return False
        adjusted = OrderIntent(
            experiment_id=intent.experiment_id,
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            signal_ts_utc=intent.signal_ts_utc,
            created_ts_utc=intent.created_ts_utc,
            earliest_exec_ts_utc=intent.earliest_exec_ts_utc,
            qty=qty,
            trigger_price=intent.trigger_price,
            limit_price=intent.limit_price,
            stop_price=intent.stop_price,
            metadata=intent.metadata,
        )
        self.order_queue.enqueue(adjusted)
        self.pending_exit_qty_by_lot[lot_id] = reserved + qty
        self.runtime_debug["order_intents_enqueued"] += 1
        self.runtime_debug["exit_orders_enqueued"] += 1
        return True

    def _on_daily_bar(self, ticker: str, bar: Any) -> None:
        self.runtime_debug["daily_bars"] += 1
        day = bar.EndTime.date()
        row = {
            "ticker": ticker,
            "trading_day": day,
            "open": float(bar.Open),
            "high": float(bar.High),
            "low": float(bar.Low),
            "close": float(bar.Close),
            "volume": float(bar.Volume),
        }
        hist = pd.concat([self.daily_history[ticker], pd.DataFrame([row])], ignore_index=True)
        self.daily_history[ticker] = hist

        signal_day = self._next_trading_day(day)
        ctx = DailyScanContext(signal_day=signal_day, generated_at_utc=datetime.now(timezone.utc))

        close_value = float(row.get("close", 0.0))
        universe_eval = self._evaluate_universe_filters(
            ticker=ticker,
            day=day,
            signal_day=signal_day,
            hist=hist,
            close_value=close_value,
        )
        passed_universe = True
        rejection_code: str | None = None
        rejection_evidence: dict[str, Any] = {}
        if isinstance(universe_eval, tuple) and len(universe_eval) == 3:
            passed_universe = bool(universe_eval[0])
            rejection_code = str(universe_eval[1]) if universe_eval[1] else None
            rejection_evidence = universe_eval[2] if isinstance(universe_eval[2], dict) else {}
        elif isinstance(universe_eval, bool):
            passed_universe = universe_eval
        if not passed_universe:
            self._trace_setup_event(
                "scanner_rejected",
                symbol=ticker,
                setup_type="scanner_global",
                scan_day=str(day),
                trade_day=str(signal_day),
                reason_code=str(rejection_code or "UNIVERSE_FILTER_REJECTED"),
                evidence=rejection_evidence,
            )
            return

        setups = []
        if self.run_config.setups.common_breakout.enabled:
            setups.extend(self.common_adapter.compute_setups(hist, ctx))
        if self.run_config.setups.episodic_pivot.enabled:
            setups.extend(self.ep_adapter.compute_setups(hist, ctx))
        self.runtime_debug["setups_generated"] += len(setups)

        intents = self.pending_intents_by_day.setdefault(signal_day, [])
        created_intents = 0
        for setup in setups:
            ladder = self._entry_ladder_for_setup(setup.setup_type)
            stop_mode = self._stop_mode_for_setup(setup.setup_type)
            stop_cap_mode = self._stop_cap_mode_for_setup(setup.setup_type)
            for stage_idx, stage_min in enumerate(ladder, start=1):
                setup_lot_id = f"{setup.symbol}|{signal_day}|{setup.setup_type}|s{stage_min}"
                stage_metadata = {
                    **setup.metadata,
                    "scan_day": str(day),
                    "trade_day": str(signal_day),
                    "entry_stage": int(stage_min),
                    "entry_stage_index": int(stage_idx),
                    "setup_lot_id": setup_lot_id,
                    "entry_ladder_minutes": ladder,
                    "stop_mode": stop_mode,
                    "stop_cap_mode": stop_cap_mode,
                }
                intents.append(
                    SetupIntent(
                        experiment_id=self.runtime_experiment_id,
                        symbol=setup.symbol,
                        setup_type=setup.setup_type,
                        source_signal_ts_utc=setup.signal_ts_utc,
                        trade_day=setup.trading_day,
                        breakout_level=None,
                        stop_level=setup.stop_level,
                        metadata=stage_metadata,
                    )
                )
                created_intents += 1
                self._trace_setup_event(
                    "candidate_created",
                    symbol=setup.symbol,
                    setup_type=setup.setup_type,
                    setup_lot_id=setup_lot_id,
                    trade_day=str(signal_day),
                    entry_stage=int(stage_min),
                    scan_day=str(day),
                )
        self.runtime_debug["intents_created"] += created_intents

        # Evaluate daily exits for open positions
        symbol_lots = self._lots_for_symbol(ticker)
        if not symbol_lots:
            return
        max_holding_days = self.run_config.execution.max_holding_days
        for lot_id, position in symbol_lots:
            if position.last_exit_signal_day == day:
                continue

            snapshot = self._position_snapshot(position)
            ma_plan = evaluate_daily_ma_exit(
                snapshot,
                hist,
                scan_day=day,
                ma_exit_execution=self._ma_exit_execution_for_setup(position.setup_type),
                trailing_switch_mode=self._trailing_switch_mode_for_setup(position.setup_type),
            )
            if ma_plan:
                intent = OrderIntent(
                    experiment_id=self.runtime_experiment_id,
                    symbol=ticker,
                    side="SELL",
                    order_type="MARKET",
                    signal_ts_utc=ma_plan.signal_ts_utc,
                    created_ts_utc=ma_plan.signal_ts_utc,
                    earliest_exec_ts_utc=ma_plan.signal_ts_utc + timedelta(minutes=1),
                    qty=ma_plan.qty,
                    metadata={
                        "role": "exit",
                        "setup_type": position.setup_type,
                        "scan_day": position.scan_day,
                        "trade_day": position.trade_day,
                        "exit_reason": ma_plan.reason,
                        "leakage_guard": "strict_t_plus_1",
                        "setup_lot_id": lot_id,
                        "entry_stage": position.entry_stage,
                        "stop_mode": position.stop_mode,
                    },
                )
                if self._queue_exit_intent(intent):
                    self.runtime_debug["ma_exit_signals"] += 1
                    position.last_exit_signal_day = day

            if max_holding_days is not None and (day - position.entry_day).days >= int(max_holding_days):
                signal_ts = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).replace(hour=23, minute=59)
                intent = OrderIntent(
                    experiment_id=self.runtime_experiment_id,
                    symbol=ticker,
                    side="SELL",
                    order_type="MARKET",
                    signal_ts_utc=signal_ts,
                    created_ts_utc=signal_ts,
                    earliest_exec_ts_utc=signal_ts + timedelta(minutes=1),
                    qty=position.qty_open,
                    metadata={
                        "role": "exit",
                        "setup_type": position.setup_type,
                        "scan_day": position.scan_day,
                        "trade_day": position.trade_day,
                        "exit_reason": "max_holding_days",
                        "leakage_guard": "strict_t_plus_1",
                        "setup_lot_id": lot_id,
                        "entry_stage": position.entry_stage,
                        "stop_mode": position.stop_mode,
                    },
                )
                if self._queue_exit_intent(intent):
                    position.last_exit_signal_day = day

    def _is_close_window(self, now_est: datetime) -> bool:
        return (now_est.hour == 15 and now_est.minute >= 58) or now_est.hour > 15

    def _submit_order_intent(self, intent: OrderIntent, now_utc: datetime) -> None:
        symbol = self.symbol_map[intent.symbol]
        signed_qty = int(intent.qty)
        if intent.side.upper() == "SELL":
            signed_qty = -abs(signed_qty)
        else:
            signed_qty = abs(signed_qty)

        if signed_qty == 0:
            return

        tag = self._build_order_tag(intent)
        ticket = self.MarketOrder(symbol, signed_qty, True, tag)
        self.runtime_debug["orders_submitted"] += 1
        self.order_meta[int(ticket.OrderId)] = {
            "symbol": intent.symbol,
            "role": str(intent.metadata.get("role", "entry")),
            "side": intent.side.upper(),
            "signal_ts_utc": intent.signal_ts_utc,
            "submit_ts_utc": now_utc,
            "setup_type": intent.metadata.get("setup_type", "unknown"),
            "setup_lot_id": str(intent.metadata.get("setup_lot_id", "")),
            "entry_stage": int(intent.metadata.get("entry_stage", 0) or 0),
            "stop_mode": str(intent.metadata.get("stop_mode", "")),
            "trade_day": intent.metadata.get("trade_day", ""),
            "scan_day": intent.metadata.get("scan_day", ""),
            "leakage_guard": intent.metadata.get("leakage_guard", "strict_t_plus_1"),
            "exit_reason": intent.metadata.get("exit_reason", ""),
            "stop_price": intent.stop_price,
            "trailing_ma_days": intent.metadata.get("trailing_ma_days"),
            "rule_version": intent.metadata.get("rule_version", ""),
        }

    @staticmethod
    def _build_order_tag(intent: OrderIntent) -> str:
        role = str(intent.metadata.get("role", "entry"))
        side = str(intent.side).upper()
        return (
            f"{ORDER_TAG_PREFIX}"
            f"role={role}|"
            f"side={side}|"
            f"symbol={intent.symbol}|"
            f"setup_type={intent.metadata.get('setup_type', 'unknown')}|"
            f"signal_ts_utc={intent.signal_ts_utc.isoformat()}|"
            f"trade_day={intent.metadata.get('trade_day', '')}|"
            f"scan_day={intent.metadata.get('scan_day', '')}|"
            f"exit_reason={intent.metadata.get('exit_reason', '')}|"
            f"leakage_guard={intent.metadata.get('leakage_guard', 'strict_t_plus_1')}|"
            f"setup_lot_id={intent.metadata.get('setup_lot_id', '')}|"
            f"entry_stage={int(intent.metadata.get('entry_stage', 0) or 0)}|"
            f"stop_mode={intent.metadata.get('stop_mode', '')}"
        )

    @staticmethod
    def _parse_order_tag(tag: str) -> dict[str, str]:
        payload = tag[len(ORDER_TAG_PREFIX) :] if tag.startswith(ORDER_TAG_PREFIX) else tag
        parsed: dict[str, str] = {}
        for part in payload.split("|"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            parsed[key] = value
        return parsed

    def _extract_order_meta(self, order_event: Any) -> dict[str, Any] | None:
        oid = int(order_event.OrderId)
        meta = self.order_meta.get(oid)
        if meta:
            return meta

        try:
            order = self.Transactions.GetOrderById(oid)
        except builtins.Exception:
            return None
        if order is None:
            return None

        tag = str(getattr(order, "Tag", "") or "")
        if not tag.startswith(ORDER_TAG_PREFIX):
            return None

        parsed = self._parse_order_tag(tag)
        signal_value = parsed.get("signal_ts_utc", "")
        signal_ts = _ts(signal_value) or datetime.now(timezone.utc)
        submit_ts = signal_ts
        return {
            "symbol": parsed.get("symbol", str(getattr(order_event, "Symbol", "") or "")),
            "role": parsed.get("role", "entry"),
            "side": parsed.get("side", "BUY"),
            "signal_ts_utc": signal_ts,
            "submit_ts_utc": submit_ts,
            "setup_type": parsed.get("setup_type", "unknown"),
            "setup_lot_id": parsed.get("setup_lot_id", ""),
            "entry_stage": int(parsed.get("entry_stage", "0") or "0"),
            "stop_mode": parsed.get("stop_mode", ""),
            "trade_day": parsed.get("trade_day", ""),
            "scan_day": parsed.get("scan_day", ""),
            "leakage_guard": parsed.get("leakage_guard", "strict_t_plus_1"),
            "exit_reason": parsed.get("exit_reason", ""),
            "stop_price": None,
            "trailing_ma_days": None,
        }

    def _apply_entry_fill(self, ticker: str, qty: int, price: float, meta: dict[str, Any]) -> None:
        if qty <= 0:
            return

        lot_id = str(meta.get("setup_lot_id", ""))
        if not lot_id:
            lot_id = f"{ticker}|{meta.get('trade_day', '')}|{meta.get('setup_type', 'unknown')}|s{int(meta.get('entry_stage', 0) or 0)}"
        position = self.positions_by_lot.get(lot_id)
        if position and position.qty_open > 0:
            total_cost = position.entry_avg_price * position.qty_open + price * qty
            new_qty = position.qty_open + qty
            position.entry_avg_price = total_cost / max(1, new_qty)
            position.qty_open = new_qty
            position.dynamic_stop = max(0.0, position.dynamic_stop)
            position.last_entry_signal_ts_utc = str(meta.get("signal_ts_utc", ""))
            return

        trade_day = _parse_date(meta.get("trade_day")) or self.Time.date()
        stop_price = _safe_float(meta.get("stop_price", 0.0), 0.0)
        if stop_price <= 0:
            stop_price = price * 0.95
        entry_stage = int(meta.get("entry_stage", 0) or 0)
        stop_mode = str(meta.get("stop_mode", "running_lod_at_entry") or "running_lod_at_entry")
        self.positions_by_lot[lot_id] = PositionState(
            setup_lot_id=lot_id,
            symbol=ticker,
            qty_open=qty,
            entry_avg_price=price,
            entry_day=trade_day,
            setup_type=str(meta.get("setup_type", "unknown")),
            entry_stage=entry_stage,
            stop_mode=stop_mode,
            initial_stop=stop_price,
            dynamic_stop=stop_price,
            partial_done=False,
            scan_day=str(meta.get("scan_day", "")),
            trade_day=str(meta.get("trade_day", "")),
            trailing_ma_days=int(meta.get("trailing_ma_days", 10) or 10),
            last_entry_signal_ts_utc=str(meta.get("signal_ts_utc", "")),
            last_exit_signal_day=None,
        )

    def _apply_exit_fill(self, ticker: str, qty: int, meta: dict[str, Any]) -> int:
        if qty <= 0:
            return 0

        lot_id = str(meta.get("setup_lot_id", ""))
        position: PositionState | None = None
        if lot_id:
            position = self.positions_by_lot.get(lot_id)
        if position is None:
            lots = self._lots_for_symbol(ticker)
            if lots:
                lot_id, position = lots[0]
        if not lot_id or not position or position.qty_open <= 0:
            self.runtime_debug["position_underflow_blocked"] += 1
            return 0

        consumed = min(qty, position.qty_open)
        position.qty_open -= consumed
        if str(meta.get("exit_reason", "")).startswith("partial_exit_day"):
            position.partial_done = True

        if position.qty_open <= 0:
            self.positions_by_lot.pop(lot_id, None)
            self.pending_exit_qty_by_lot.pop(lot_id, None)
            if not self._lots_for_symbol(ticker):
                self.pending_symbols.discard(ticker)
        return consumed

    def _queue_forced_flatten(self, ticker: str, now_utc: datetime, reason: str) -> None:
        symbol_lots = self._lots_for_symbol(ticker)
        for lot_id, position in symbol_lots:
            intent = OrderIntent(
                experiment_id=self.runtime_experiment_id,
                symbol=ticker,
                side="SELL",
                order_type="MARKET",
                signal_ts_utc=now_utc,
                created_ts_utc=now_utc,
                earliest_exec_ts_utc=now_utc + timedelta(minutes=1),
                qty=position.qty_open,
                metadata={
                    "role": "exit",
                    "setup_type": position.setup_type,
                    "scan_day": position.scan_day,
                    "trade_day": position.trade_day,
                    "exit_reason": reason,
                    "leakage_guard": "strict_t_plus_1",
                    "setup_lot_id": lot_id,
                    "entry_stage": position.entry_stage,
                    "stop_mode": position.stop_mode,
                },
            )
            if self._queue_exit_intent(intent):
                self.runtime_debug["forced_flatten_signals"] += 1

    def _process_exit_rules(self, data: Any, now_est: datetime, now_utc: datetime) -> None:
        close_window = self._is_close_window(now_est)
        end_flatten = (
            self.run_config.execution.force_flatten_on_end
            and now_est.date() == self.run_config.period.end_date
            and close_window
        )
        flatten_reason = "force_flatten_end" if end_flatten else "no_overnight"
        flattened_symbols: set[str] = set()

        for lot_id, position in list(self.positions_by_lot.items()):
            ticker = position.symbol
            symbol = self.symbol_map.get(ticker)
            if symbol is None or symbol not in data.Bars:
                continue
            bar = data.Bars[symbol]
            snapshot = self._position_snapshot(position)

            stop_intent = evaluate_intraday_stop(snapshot, bar, now_utc)
            if stop_intent:
                stop_intent = OrderIntent(
                    experiment_id=stop_intent.experiment_id,
                    symbol=stop_intent.symbol,
                    side=stop_intent.side,
                    order_type=stop_intent.order_type,
                    signal_ts_utc=stop_intent.signal_ts_utc,
                    created_ts_utc=stop_intent.created_ts_utc,
                    earliest_exec_ts_utc=stop_intent.earliest_exec_ts_utc,
                    qty=stop_intent.qty,
                    trigger_price=stop_intent.trigger_price,
                    limit_price=stop_intent.limit_price,
                    stop_price=stop_intent.stop_price,
                    metadata={
                        **stop_intent.metadata,
                        "setup_lot_id": lot_id,
                        "entry_stage": position.entry_stage,
                        "stop_mode": position.stop_mode,
                    },
                )
            if stop_intent and self._queue_exit_intent(stop_intent):
                self.runtime_debug["intraday_stop_signals"] += 1

            partial_intent = evaluate_partial_exit(snapshot, now_est, self.run_config.setups.common_breakout)
            if partial_intent:
                partial_intent = OrderIntent(
                    experiment_id=partial_intent.experiment_id,
                    symbol=partial_intent.symbol,
                    side=partial_intent.side,
                    order_type=partial_intent.order_type,
                    signal_ts_utc=partial_intent.signal_ts_utc,
                    created_ts_utc=partial_intent.created_ts_utc,
                    earliest_exec_ts_utc=partial_intent.earliest_exec_ts_utc,
                    qty=partial_intent.qty,
                    trigger_price=partial_intent.trigger_price,
                    limit_price=partial_intent.limit_price,
                    stop_price=partial_intent.stop_price,
                    metadata={
                        **partial_intent.metadata,
                        "setup_lot_id": lot_id,
                        "entry_stage": position.entry_stage,
                        "stop_mode": position.stop_mode,
                    },
                )
            if partial_intent and self._queue_exit_intent(partial_intent):
                self.runtime_debug["partial_exit_signals"] += 1

            if (
                (not self.run_config.execution.allow_overnight and close_window) or end_flatten
            ) and ticker not in flattened_symbols:
                self._queue_forced_flatten(ticker, now_utc, flatten_reason)
                flattened_symbols.add(ticker)

    def _orh_window_for_setup(self, setup_type: str) -> int:
        if setup_type == "common_breakout":
            return int(self.run_config.setups.common_breakout.orh_window_minutes)
        if setup_type == "episodic_pivot":
            return int(self.run_config.setups.episodic_pivot.orh_window_minutes)
        return 5

    def _get_intraday_state(
        self,
        ticker: str,
        day: date,
        now_est: datetime,
        setup_type: str = "",
        *,
        orh_window_override: int | None = None,
        state_suffix: str = "",
    ) -> IntradayState:
        orh_window = int(orh_window_override) if orh_window_override is not None else (
            self._orh_window_for_setup(setup_type) if setup_type else 5
        )
        suffix = f"|{state_suffix}" if state_suffix else ""
        cache_key = f"{ticker}|{setup_type}|w{orh_window}{suffix}" if setup_type else f"{ticker}|w{orh_window}{suffix}"
        state = self.intraday_state.get(cache_key)
        if state and state.day == day:
            return state

        session_open = now_est.replace(hour=9, minute=30, second=0, microsecond=0)
        state = IntradayState(
            day=day,
            orh_window_minutes=orh_window,
            open_range_end_est=session_open + timedelta(minutes=orh_window),
        )
        self.intraday_state[cache_key] = state
        return state

    def _process_entry_rules(self, data: Any, now_est: datetime, now_utc: datetime) -> None:
        today = now_est.date()
        day_intents = self.pending_intents_by_day.get(today, [])
        if not day_intents:
            return

        reserved_margin = 0.0
        opening_context_updated: set[str] = set()
        for intent in sorted(
            day_intents,
            key=lambda item: (
                item.symbol,
                item.setup_type,
                int(item.metadata.get("entry_stage", 0) or 0),
                str(item.metadata.get("setup_lot_id", "")),
            ),
        ):
            ticker = intent.symbol
            lot_id = str(intent.metadata.get("setup_lot_id", ""))
            if not lot_id:
                continue
            if lot_id in self.submitted_lot_ids or lot_id in self.blocked_lot_ids_today or lot_id in self.positions_by_lot:
                continue

            symbol = self.symbol_map.get(ticker)
            if symbol is None or symbol not in data.Bars:
                continue
            bar = data.Bars[symbol]
            if intent.setup_type == "episodic_pivot" and ticker not in opening_context_updated:
                self._update_opening_context(ticker, bar, now_est)
                opening_context_updated.add(ticker)
            bar_high = float(getattr(bar, "High", getattr(bar, "high", 0.0)) or 0.0)
            bar_low = float(getattr(bar, "Low", getattr(bar, "low", 0.0)) or 0.0)
            if bar_high <= 0:
                continue

            stage_min = int(intent.metadata.get("entry_stage", 1) or 1)
            stage_index = int(intent.metadata.get("entry_stage_index", 1) or 1)
            trade_day_for_prefix = str(intent.metadata.get("trade_day", str(today)))
            if stage_index > 1:
                family_prefix = f"{ticker}|{trade_day_for_prefix}|{intent.setup_type}|"
                has_open_family_lot = any(
                    lot_key.startswith(family_prefix) and pos.qty_open > 0
                    for lot_key, pos in self.positions_by_lot.items()
                )
                if not has_open_family_lot:
                    continue
            state = self._get_intraday_state(
                ticker,
                today,
                now_est,
                setup_type=intent.setup_type,
                orh_window_override=stage_min,
                state_suffix=f"{intent.setup_type}|s{stage_min}",
            )
            if state.open_range_high is None:
                state.open_range_high = bar_high
            else:
                state.open_range_high = max(state.open_range_high, bar_high)
            if state.running_day_low is None:
                state.running_day_low = bar_low
            else:
                state.running_day_low = min(state.running_day_low, bar_low)
            if state.open_range_end_est is not None and now_est >= state.open_range_end_est:
                state.orh_ready = True
            if not state.orh_ready:
                continue

            trigger = float(state.open_range_high or bar_high)
            if bar_high < trigger:
                continue

            ep_gate = self._evaluate_ep_activation(ticker=ticker, intent=intent, now_est=now_est, bar=bar)
            if ep_gate.get("status") == "pending":
                continue
            if ep_gate.get("status") == "fail":
                self.runtime_debug["entry_blocked_ep_gate"] += 1
                self.blocked_lot_ids_today.add(lot_id)
                self._trace_setup_event(
                    "entry_rejected",
                    symbol=ticker,
                    setup_type=intent.setup_type,
                    setup_lot_id=lot_id,
                    trade_day=str(today),
                    entry_stage=stage_min,
                    reason_code=str(ep_gate.get("code", "EP_GATE_FAILED")),
                    message=str(ep_gate.get("message", "EP activation failed")),
                    evidence=ep_gate.get("evidence", {}),
                )
                continue

            stop_mode = self._normalize_stop_mode(str(intent.metadata.get("stop_mode", "")))
            stop_price, stop_status = self._resolve_stop_price_strict(
                ticker=ticker,
                today=today,
                bar=bar,
                intraday_state=state,
                stop_mode=stop_mode,
            )
            if stop_price is None:
                self.runtime_debug["entry_blocked_stop_data_missing"] += 1
                self.blocked_lot_ids_today.add(lot_id)
                self._trace_setup_event(
                    "entry_rejected",
                    symbol=ticker,
                    setup_type=intent.setup_type,
                    setup_lot_id=lot_id,
                    trade_day=str(today),
                    entry_stage=stage_min,
                    reason_code=stop_status,
                    message="Stop reference missing/invalid",
                    evidence={"stop_mode": stop_mode},
                )
                continue

            stop_cap_mode = self._stop_cap_mode_for_setup(intent.setup_type)
            cap_decision = self._evaluate_stop_cap(
                ticker=ticker,
                today=today,
                setup_type=intent.setup_type,
                stop_cap_mode=stop_cap_mode,
                entry_price=trigger,
                stop_price=stop_price,
            )
            if cap_decision.get("decision") == "block":
                self.runtime_debug["entry_blocked_stop_cap"] += 1
                self.blocked_lot_ids_today.add(lot_id)
                self._trace_setup_event(
                    "entry_rejected",
                    symbol=ticker,
                    setup_type=intent.setup_type,
                    setup_lot_id=lot_id,
                    trade_day=str(today),
                    entry_stage=stage_min,
                    reason_code=str(cap_decision.get("code", "STOP_CAP_BLOCKED")),
                    message="Stop cap hard block",
                    evidence=cap_decision.get("evidence", {}),
                )
                continue
            if cap_decision.get("decision") == "warn":
                self.runtime_debug["entry_warn_stop_cap"] += 1
                self._trace_setup_event(
                    "entry_warning",
                    symbol=ticker,
                    setup_type=intent.setup_type,
                    setup_lot_id=lot_id,
                    trade_day=str(today),
                    entry_stage=stage_min,
                    warning_code=str(cap_decision.get("code", "STOP_CAP_WARN")),
                    message="Stop cap warning",
                    evidence=cap_decision.get("evidence", {}),
                )

            qty = calc_position_size(
                equity=float(self.Portfolio.TotalPortfolioValue),
                entry_price=trigger,
                stop_price=stop_price,
                risk_per_trade=self.run_config.execution.risk_per_trade,
                current_positions=sum(1 for p in self.positions_by_lot.values() if p.qty_open > 0),
                max_positions=self.run_config.execution.max_positions,
            )
            if qty <= 0:
                self.blocked_lot_ids_today.add(lot_id)
                self._trace_setup_event(
                    "entry_rejected",
                    symbol=ticker,
                    setup_type=intent.setup_type,
                    setup_lot_id=lot_id,
                    trade_day=str(today),
                    entry_stage=stage_min,
                    reason_code="ENTRY_QTY_ZERO",
                    message="Position sizing returned qty=0",
                    evidence={"trigger": trigger, "stop_price": stop_price},
                )
                continue

            leverage = 1.0
            margin_remaining = 0.0
            try:
                leverage = float(getattr(self.Securities[symbol], "Leverage", 1.0) or 1.0)
            except builtins.Exception:
                leverage = 1.0
            try:
                margin_remaining = float(self.Portfolio.MarginRemaining)
            except builtins.Exception:
                margin_remaining = 0.0

            self.runtime_debug["entry_qty_requested_total"] += int(qty)
            capped_qty = cap_qty_by_margin(
                requested_qty=qty,
                entry_price=trigger,
                leverage=leverage,
                margin_remaining=margin_remaining,
                reserved_margin=reserved_margin,
                margin_buffer_pct=self.run_config.execution.entry_margin_buffer_pct,
            )
            final_qty = min(int(qty), int(capped_qty))
            margin_capped = final_qty < int(qty)
            if margin_capped:
                self.runtime_debug["entry_qty_downsized_events"] += 1
            if final_qty <= 0:
                self.runtime_debug["entry_blocked_no_margin"] += 1
                self.blocked_lot_ids_today.add(lot_id)
                self._trace_setup_event(
                    "entry_rejected",
                    symbol=ticker,
                    setup_type=intent.setup_type,
                    setup_lot_id=lot_id,
                    trade_day=str(today),
                    entry_stage=stage_min,
                    reason_code="ENTRY_BLOCKED_NO_MARGIN",
                    message="Insufficient margin after cap",
                    evidence={
                        "trigger": trigger,
                        "requested_qty": int(qty),
                        "final_qty": int(final_qty),
                        "margin_remaining": margin_remaining,
                        "reserved_margin": reserved_margin,
                    },
                )
                continue
            self.runtime_debug["entry_qty_submitted_total"] += int(final_qty)
            reserved_margin += (trigger * float(final_qty)) / max(1.0, leverage)

            signal_ts = now_utc
            order_intent = OrderIntent(
                experiment_id=self.runtime_experiment_id,
                symbol=ticker,
                side="BUY",
                order_type="MARKET",
                signal_ts_utc=signal_ts,
                created_ts_utc=signal_ts,
                earliest_exec_ts_utc=signal_ts + timedelta(minutes=1),
                qty=final_qty,
                trigger_price=trigger,
                stop_price=stop_price,
                metadata={
                    "role": "entry",
                    "setup_type": intent.setup_type,
                    "setup_lot_id": lot_id,
                    "entry_stage": stage_min,
                    "stop_mode": stop_mode,
                    "stop_cap_mode": stop_cap_mode,
                    "trade_day": str(today),
                    "scan_day": str(intent.metadata.get("scan_day", "")),
                    "leakage_guard": "strict_t_plus_1",
                    "stop_price": stop_price,
                    "trailing_ma_days": self._trailing_ma_days_for_setup(intent.setup_type),
                    "rule_version": str(intent.metadata.get("rule_version", "")),
                    "qty_requested": int(qty),
                    "qty_final": int(final_qty),
                    "margin_capped": bool(margin_capped),
                },
            )
            self.order_queue.enqueue(order_intent)
            self.submitted_lot_ids.add(lot_id)
            self.runtime_debug["order_intents_enqueued"] += 1
            self.runtime_debug["entry_orders_enqueued"] += 1
            self._trace_setup_event(
                "entry_submitted",
                symbol=ticker,
                setup_type=intent.setup_type,
                setup_lot_id=lot_id,
                trade_day=str(today),
                entry_stage=stage_min,
                trigger=trigger,
                stop_price=stop_price,
                qty=int(final_qty),
                margin_capped=bool(margin_capped),
            )

    def OnData(self, data: Any) -> None:  # noqa: N802
        if not hasattr(self, "Time") or not hasattr(self, "UtcTime"):
            return

        now_est = self.Time
        now_utc = self.UtcTime

        if self.current_day != now_est.date():
            self.current_day = now_est.date()
            self.pending_symbols.clear()
            self.blocked_lot_ids_today.clear()
            self.submitted_lot_ids.clear()
            self.opening_context_by_ticker.clear()

        self._process_exit_rules(data, now_est, now_utc)
        self._process_entry_rules(data, now_est, now_utc)

        for intent in self.order_queue.pop_ready(now_utc):
            self._submit_order_intent(intent, now_utc)

    def OnOrderEvent(self, orderEvent: Any) -> None:  # noqa: N802
        if int(orderEvent.Status) != 3:  # Filled
            return

        meta = self._extract_order_meta(orderEvent)
        if not meta:
            return

        oid = int(orderEvent.OrderId)
        raw_fill_qty = int(orderEvent.FillQuantity)
        if raw_fill_qty == 0:
            return

        side = str(meta.get("side", "BUY")).upper()
        role = str(meta.get("role", "entry")).lower()
        if role not in {"entry", "exit"}:
            role = "entry" if side == "BUY" else "exit"

        qty_abs = abs(raw_fill_qty)
        ticker = str(meta.get("symbol", "") or getattr(orderEvent, "Symbol", "") or "").upper()
        if role == "exit":
            qty_abs = self._apply_exit_fill(ticker, qty_abs, meta)
            if qty_abs <= 0:
                return
            lot_id = str(meta.get("setup_lot_id", ""))
            if lot_id:
                self.pending_exit_qty_by_lot[lot_id] = max(0, self.pending_exit_qty_by_lot.get(lot_id, 0) - qty_abs)
            self.runtime_debug["exit_fills"] += 1
        else:
            self._apply_entry_fill(ticker, qty_abs, float(orderEvent.FillPrice), meta)
            self.runtime_debug["entry_fills"] += 1

        fee = 0.0
        try:
            fee = float(orderEvent.OrderFee.Value.Amount)
        except builtins.Exception:
            try:
                fee = float(orderEvent.OrderFee.Amount)
            except builtins.Exception:
                fee = 0.0

        signal_ts = meta.get("signal_ts_utc")
        submit_ts = meta.get("submit_ts_utc")
        signal_ts_iso = signal_ts.isoformat() if isinstance(signal_ts, datetime) else str(signal_ts or "")
        submit_ts_iso = submit_ts.isoformat() if isinstance(submit_ts, datetime) else str(submit_ts or "")

        self.fill_events.append(
            {
                "experiment_id": self.runtime_experiment_id,
                "order_id": oid,
                "symbol": ticker,
                "side": "SELL" if role == "exit" else "BUY",
                "role": role,
                "setup_type": str(meta.get("setup_type", "unknown")),
                "setup_lot_id": str(meta.get("setup_lot_id", "")),
                "entry_stage": int(meta.get("entry_stage", 0) or 0),
                "stop_mode": str(meta.get("stop_mode", "")),
                "scan_day": str(meta.get("scan_day", "")),
                "trade_day": str(meta.get("trade_day", "")),
                "order_type": "MARKET",
                "signal_ts_utc": signal_ts_iso,
                "submit_ts_utc": submit_ts_iso,
                "fill_ts_utc": str(self.UtcTime),
                "qty": int(qty_abs),
                "fill_quantity": -int(qty_abs) if role == "exit" else int(qty_abs),
                "fill_price": float(orderEvent.FillPrice),
                "fee": fee,
                "slippage": 0.0,
                "exit_reason": str(meta.get("exit_reason", "")),
                "leakage_guard": str(meta.get("leakage_guard", "strict_t_plus_1")),
                "rule_version": str(meta.get("rule_version", "")),
            }
        )

    def OnEndOfAlgorithm(self) -> None:  # noqa: N802
        results_dir = Path("/Lean/Results")
        results_dir.mkdir(parents=True, exist_ok=True)

        fills_sorted = sorted(
            self.fill_events,
            key=lambda item: (
                str(item.get("fill_ts_utc", "")),
                int(item.get("order_id", 0)),
                str(item.get("symbol", "")),
            ),
        )
        fills_path = results_dir / "lean_fills_raw.json"
        fills_path.write_text(json.dumps(fills_sorted, indent=2, sort_keys=True, default=str), encoding="utf-8")

        trades = build_trade_records_from_fills(fills_sorted, self.runtime_experiment_id)
        trades_path = results_dir / "lean_trades_raw.json"
        trades_path.write_text(json.dumps(trades, indent=2, sort_keys=True, default=str), encoding="utf-8")

        debug_path = results_dir / "lean_runtime_debug.json"
        debug_path.write_text(json.dumps(self.runtime_debug, indent=2, sort_keys=True), encoding="utf-8")

        daily_history_stats: dict[str, Any] = {}
        for _, hist in self.daily_history.items():
            if hist.empty:
                continue
            ticker = str(hist["ticker"].iloc[-1])
            daily_history_stats[ticker] = {
                "rows": int(len(hist)),
                "first_day": str(pd.to_datetime(hist["trading_day"]).dt.date.min()),
                "last_day": str(pd.to_datetime(hist["trading_day"]).dt.date.max()),
                "avg_volume": float(pd.to_numeric(hist["volume"], errors="coerce").mean()),
                "min_close": float(pd.to_numeric(hist["close"], errors="coerce").min()),
                "max_close": float(pd.to_numeric(hist["close"], errors="coerce").max()),
            }
        stats_path = results_dir / "lean_daily_history_stats.json"
        stats_path.write_text(json.dumps(daily_history_stats, indent=2, sort_keys=True), encoding="utf-8")

        setup_trace_path = results_dir / "setup_trace.json"
        setup_trace_path.write_text(
            json.dumps(self.setup_trace_events, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
