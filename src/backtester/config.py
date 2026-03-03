"""Unified configuration models for BacktesterV3.

All config is defined as Pydantic models. Loaded from YAML, validated on construction.
No config value is ever hardcoded in the engine — everything flows through here.

Parameter ownership:
- data.*       → WHERE to find OHLCV data
- universe.*   → WHICH tickers to test
- setups.*     → SCAN: what signals to generate (per-strategy params)
- execution.*  → SIMULATION: how to trade those signals (shared across strategies)
- runner.*     → INFRASTRUCTURE: how LEAN runs (hidden from casual users)
- output.*     → WHERE to write results
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_FROZEN = ConfigDict(frozen=True)


# ---------------------------------------------------------------------------
# Data config
# ---------------------------------------------------------------------------


class DataConfig(BaseModel):
    model_config = _FROZEN

    parquet_root: Path
    lean_data_root: Path = Path("outputs/lean_data")
    cache_root: Path = Path("outputs/cache")
    timezone: str = "America/New_York"
    rth_start: str = "09:30"
    rth_end: str = "16:00"
    timestamp_alignment: Literal["auto", "utc", "eastern"] = "auto"
    source_layout: Literal["symbol_year"] = "symbol_year"
    lean_feed_scope: Literal["full", "rth"] = "full"
    split_adjustment_mode: Literal["none", "splits_backward"] = "none"
    split_events_file: Path = Path(
        "/mnt/Daten/Backtest_data/Stock_splits_corporate_actions/hf_defeatbeta_stock_split_events_2026-02-16.parquet"
    )
    split_adjust_volume: bool = True


# ---------------------------------------------------------------------------
# Universe config
# ---------------------------------------------------------------------------


class UniverseFiltersConfig(BaseModel):
    model_config = _FROZEN

    min_price: float | None = 5.0
    max_price: float | None = None
    min_avg_dollar_vol_20: float | None = 1_000_000.0
    min_adr_pct: float | None = 3.0
    exclude_otc: bool = True

    @field_validator("min_price", "max_price", "min_avg_dollar_vol_20", "min_adr_pct")
    @classmethod
    def validate_non_negative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("must be >= 0")
        return value

    @model_validator(mode="after")
    def validate_min_max(self) -> UniverseFiltersConfig:
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("min_price must be <= max_price")
        return self


class UniverseConfig(BaseModel):
    model_config = _FROZEN

    mode: Literal["custom_list", "all"] = "custom_list"
    tickers: list[str] = Field(default_factory=list)
    tickers_csv: Path | None = None
    filters: UniverseFiltersConfig = Field(default_factory=UniverseFiltersConfig)

    @field_validator("tickers")
    @classmethod
    def normalize_tickers(cls, value: list[str]) -> list[str]:
        cleaned = {t.strip().upper() for t in value if t and t.strip()}
        return sorted(cleaned)


# ---------------------------------------------------------------------------
# Period config
# ---------------------------------------------------------------------------


class PeriodConfig(BaseModel):
    model_config = _FROZEN

    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_range(self) -> PeriodConfig:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be >= start_date")
        return self


# ---------------------------------------------------------------------------
# Strategy setup configs (per-strategy parameters)
# ---------------------------------------------------------------------------


class CommonBreakoutConfig(BaseModel):
    model_config = _FROZEN

    enabled: bool = True
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
    ma_alignment_periods: list[int] = Field(default_factory=lambda: [20, 50])
    ma_alignment_tolerance_pct: float = 3.0
    require_volume_contraction: bool = True
    max_consolidation_volume_ratio: float = 0.7
    orh_window_minutes: int = 5
    entry_ladder_minutes: list[int] = Field(default_factory=lambda: [1, 5, 60])
    stop_mode: Literal["running_lod", "running_lod_at_entry", "dminus1_open", "dminus1_low"] = "running_lod"
    stop_cap_mode: Literal["hard", "warn", "off"] = "hard"
    atr_period: int = 14
    adr_period: int = 21
    max_stop_multiple: float = 1.0
    partial_exit_day: int = 3
    partial_exit_fraction: float = 0.3333
    partial_exit_time: Literal["any_bar", "open", "close_window"] = "any_bar"
    move_stop_to_be_after_partial: bool = True
    trailing_ma_days: int = 10
    ma_exit_execution: Literal["next_open", "moc"] = "next_open"

    @field_validator("entry_ladder_minutes")
    @classmethod
    def validate_entry_ladder(cls, value: list[int]) -> list[int]:
        ordered: list[int] = []
        for item in value:
            minute = int(item)
            if minute <= 0:
                raise ValueError("entry_ladder_minutes values must be > 0")
            if minute not in ordered:
                ordered.append(minute)
        if not ordered:
            raise ValueError("entry_ladder_minutes must not be empty")
        return ordered


class EpisodicPivotConfig(BaseModel):
    model_config = _FROZEN

    enabled: bool = True
    min_gap_pct: float = 10.0
    min_opening_volume_ratio: float = 3.0
    opening_volume_window_minutes: int = 20
    ah_pm_mode: Literal["off", "proxy", "full"] = "off"
    orh_window_minutes: int = 5
    entry_ladder_minutes: list[int] = Field(default_factory=lambda: [1, 5, 60])
    stop_mode: Literal["running_lod", "running_lod_at_entry", "dminus1_open", "dminus1_low"] = "running_lod"
    stop_cap_mode: Literal["hard", "warn", "off"] = "hard"
    atr_period: int = 14
    adr_period: int = 21
    max_stop_multiple: float = 1.0
    max_stop_multiple_hard: float = 1.5
    max_prior_runup_pct: float | None = 100.0
    trailing_ma_days: int = 20
    trailing_switch_mode: Literal["ma_above_initial_stop"] = "ma_above_initial_stop"
    ma_exit_execution: Literal["next_open", "moc"] = "next_open"
    allow_scale_in: bool = False

    @field_validator("entry_ladder_minutes")
    @classmethod
    def validate_entry_ladder(cls, value: list[int]) -> list[int]:
        ordered: list[int] = []
        for item in value:
            minute = int(item)
            if minute <= 0:
                raise ValueError("entry_ladder_minutes values must be > 0")
            if minute not in ordered:
                ordered.append(minute)
        if not ordered:
            raise ValueError("entry_ladder_minutes must not be empty")
        return ordered

    @field_validator("max_prior_runup_pct")
    @classmethod
    def validate_max_prior_runup_pct(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("max_prior_runup_pct must be > 0 when set")
        return value

    @model_validator(mode="after")
    def validate_stop_multiples(self) -> EpisodicPivotConfig:
        if self.max_stop_multiple_hard < self.max_stop_multiple:
            raise ValueError("max_stop_multiple_hard must be >= max_stop_multiple")
        return self


class SetupsConfig(BaseModel):
    model_config = _FROZEN

    common_breakout: CommonBreakoutConfig = Field(default_factory=CommonBreakoutConfig)
    episodic_pivot: EpisodicPivotConfig = Field(default_factory=EpisodicPivotConfig)

    @model_validator(mode="after")
    def at_least_one_enabled(self) -> SetupsConfig:
        if not self.common_breakout.enabled and not self.episodic_pivot.enabled:
            raise ValueError("At least one setup must be enabled")
        return self


# ---------------------------------------------------------------------------
# Execution config (shared across strategies)
# ---------------------------------------------------------------------------


class ExecutionConfig(BaseModel):
    model_config = _FROZEN

    initial_cash: float = 100_000.0
    max_positions: int = 10
    risk_per_trade: float = 0.01
    commission_per_share: float = 0.005
    min_commission: float = 1.0
    slippage_bps: float = 5.0
    leakage_guard: Literal["strict_t_plus_1"] = "strict_t_plus_1"
    order_session_scope: Literal["rth_only", "full"] = "rth_only"
    entry_margin_buffer_pct: float = 0.02
    max_order_error_rate: float = 0.01
    long_only: bool = True
    force_flatten_on_end: bool = True
    allow_overnight: bool = True
    max_holding_days: int | None = None

    @field_validator("risk_per_trade", "entry_margin_buffer_pct", "max_order_error_rate")
    @classmethod
    def validate_rate_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("must be in [0.0, 1.0]")
        return value


# ---------------------------------------------------------------------------
# Runner config (LEAN infrastructure — hidden from casual users)
# ---------------------------------------------------------------------------


class RunnerConfig(BaseModel):
    model_config = _FROZEN

    mode: Literal["auto", "docker", "dotnet"] = "auto"
    docker_image: str = "quantconnect/lean:latest"
    lean_repo_path: Path | None = None
    algorithm_file: str = "algorithm/QM_MVP.py"
    auto_convert_data: bool = True
    validate_lean_data: bool = False


# ---------------------------------------------------------------------------
# Output config
# ---------------------------------------------------------------------------


class OutputConfig(BaseModel):
    model_config = _FROZEN

    dir: Path = Path("outputs")
    generate_charts: bool = True
    generate_html_report: bool = True


# ---------------------------------------------------------------------------
# Top-level RunConfig
# ---------------------------------------------------------------------------


class RunConfig(BaseModel):
    model_config = _FROZEN

    schema_version: str = "1.0.0"
    run_name: str = "backtest_run"

    data: DataConfig
    universe: UniverseConfig
    period: PeriodConfig
    setups: SetupsConfig = Field(default_factory=SetupsConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    runner: RunnerConfig = Field(default_factory=RunnerConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    def resolved_tickers(self, config_dir: Path | None = None) -> list[str]:
        tickers = list(self.universe.tickers)
        if self.universe.tickers_csv:
            csv_path = self.universe.tickers_csv
            if not csv_path.is_absolute() and config_dir:
                csv_path = (config_dir / csv_path).resolve()
            text = csv_path.read_text(encoding="utf-8")
            for raw in text.replace("\n", ",").split(","):
                if raw.strip():
                    tickers.append(raw.strip())
        return sorted({t.upper() for t in tickers if t})


# ---------------------------------------------------------------------------
# Sweep config
# ---------------------------------------------------------------------------


class SweepGridConfig(BaseModel):
    model_config = _FROZEN

    values: dict[str, list[Any]] = Field(default_factory=dict)


class SweepConfig(BaseModel):
    model_config = _FROZEN

    schema_version: str = "1.0.0"
    base_config: Path
    run_prefix: str = "sweep"
    serial: bool = True
    grid: SweepGridConfig


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_run_config(path: Path) -> RunConfig:
    raw = load_yaml(path)
    return RunConfig.model_validate(raw)


def load_sweep_config(path: Path) -> SweepConfig:
    raw = load_yaml(path)
    return SweepConfig.model_validate(raw)
