"""Domain models for BacktesterV3.

Frozen dataclasses for immutability and auditability.
These are the data structures that flow between engine stages.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MinuteBar:
    symbol: str
    ts_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class DailyBar:
    symbol: str
    trading_day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyScanContext:
    signal_day: date
    generated_at_utc: datetime
    timezone: str = "America/New_York"


@dataclass(frozen=True)
class SetupSignal:
    symbol: str
    setup_type: str
    signal_ts_utc: datetime
    trading_day: date
    breakout_level: float | None = None
    stop_level: float | None = None
    validity_end_ts_utc: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SetupIntent:
    experiment_id: str
    symbol: str
    setup_type: str
    source_signal_ts_utc: datetime
    trade_day: date
    breakout_level: float | None
    stop_level: float | None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderIntent:
    experiment_id: str
    symbol: str
    side: str
    order_type: str
    signal_ts_utc: datetime
    created_ts_utc: datetime
    earliest_exec_ts_utc: datetime
    qty: int
    trigger_price: float | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FillRecord:
    experiment_id: str
    order_id: int
    symbol: str
    side: str
    role: str
    setup_type: str
    scan_day: str
    trade_day: str
    order_type: str
    signal_ts_utc: datetime
    submit_ts_utc: datetime
    fill_ts_utc: datetime
    qty: int
    fill_price: float
    fee: float = 0.0
    slippage: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TradeRecord:
    trade_id: str
    experiment_id: str
    symbol: str
    setup_type: str
    entry_signal_ts_utc: datetime
    entry_fill_ts_utc: datetime
    entry_price: float
    entry_qty: int
    exit_fill_ts_utc: datetime | None
    exit_price: float | None
    exit_qty: int | None
    holding_minutes: float | None = None
    pnl_gross: float | None = None
    pnl_net: float | None = None
    return_pct: float | None = None
    exit_reason: str | None = None
    scan_day: str = ""
    trade_day: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EquityPoint:
    date: date
    equity: float
    drawdown_pct: float = 0.0


@dataclass(frozen=True)
class BacktestMetrics:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    total_pnl_gross: float = 0.0
    total_pnl_net: float = 0.0
    profit_factor: float = 0.0
    avg_r: float = 0.0
    median_r: float = 0.0
    r_std: float = 0.0
    expectancy: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_duration_days: int = 0
    avg_holding_minutes: float = 0.0
    max_concurrent_positions: int = 0
    sharpe_ratio: float = 0.0
    total_commission: float = 0.0
    total_slippage: float = 0.0
    exit_reason_distribution: dict[str, int] = field(default_factory=dict)
    monthly_r: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditResult:
    check_name: str
    passed: bool
    severity: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def to_dict(instance: Any) -> dict[str, Any]:
    return asdict(instance)
