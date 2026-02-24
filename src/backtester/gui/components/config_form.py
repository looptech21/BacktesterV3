"""Config editor component (curated form + YAML toggle)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Callable

from nicegui import ui

from backtester.config import RunConfig
from backtester.gui.services.config_service import ConfigService


def _to_int_list(raw: Any) -> list[int]:
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []
    values: list[int] = []
    for token in text.replace(";", ",").split(","):
        t = token.strip()
        if not t:
            continue
        values.append(int(t))
    return values


def _to_str_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []
    return [part.strip().upper() for part in text.replace("\n", ",").split(",") if part.strip()]


def _to_optional_float(raw: Any) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    return float(text)


def _to_optional_int(raw: Any) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    return int(text)


class ConfigForm:
    """Interactive RunConfig editor with form/YAML modes."""

    def __init__(
        self,
        *,
        config_service: ConfigService,
        initial_config: RunConfig,
        on_change: Callable[[RunConfig], None] | None = None,
    ) -> None:
        self.config_service = config_service
        self.config = initial_config
        self.on_change = on_change
        self.controls: dict[str, Any] = {}
        self._error_label = None
        self._mode_toggle = None
        self._yaml_editor = None
        self._form_container = None
        self._yaml_container = None
        self._period_hint_label = None

        self.root = ui.column().classes("w-full gap-3")
        with self.root:
            self._mode_toggle = ui.toggle(
                options={"form": "Form", "yaml": "YAML"},
                value="form",
                on_change=lambda _: self._on_mode_changed(),
            ).props("unelevated color=primary")
            self._error_label = ui.label("").classes("text-negative text-sm")
            self._form_container = ui.column().classes("w-full gap-3")
            self._yaml_container = ui.column().classes("w-full gap-2")
            with self._yaml_container:
                self._yaml_editor = ui.textarea(
                    label="RunConfig YAML",
                    value=self.config_service.config_to_yaml(self.config),
                ).props("autogrow").classes("w-full font-mono")
                with ui.row().classes("gap-2"):
                    ui.button("Apply YAML", on_click=self._apply_yaml).props("color=primary")
                    ui.button("Revert YAML", on_click=self._reset_yaml).props("flat")

        self._render_form_fields()
        self._set_mode("form")

    def get_config(self) -> RunConfig:
        return self.config

    def set_config(self, config: RunConfig) -> None:
        self.config = config
        self._render_form_fields()
        if self._yaml_editor:
            self._yaml_editor.value = self.config_service.config_to_yaml(config)
        self._set_error("")
        if self.on_change is not None:
            self.on_change(config)

    # ------------------------------------------------------------------
    # Internal rendering helpers
    # ------------------------------------------------------------------

    def _render_form_fields(self) -> None:
        if self._form_container is None:
            return
        self.controls.clear()
        self._form_container.clear()
        cfg = self.config

        with self._form_container:
            with ui.card().classes("w-full"):
                ui.label("Run Setup").classes("text-lg font-semibold")
                with ui.grid(columns=2).classes("w-full gap-3"):
                    self.controls["run_name"] = ui.input(
                        "Run Name",
                        value=cfg.run_name,
                        on_change=lambda _: self._apply_form(),
                    )
                    self.controls["output_dir"] = ui.input(
                        "Output Directory",
                        value=str(cfg.output.dir),
                        on_change=lambda _: self._apply_form(),
                    )

            with ui.card().classes("w-full"):
                ui.label("Period").classes("text-lg font-semibold")
                with ui.grid(columns=2).classes("w-full gap-3"):
                    self.controls["period.start_date"] = ui.input(
                        "Start Date (YYYY-MM-DD)",
                        value=str(cfg.period.start_date),
                        on_change=lambda _: self._apply_form(),
                    )
                    self.controls["period.end_date"] = ui.input(
                        "End Date (YYYY-MM-DD)",
                        value=str(cfg.period.end_date),
                        on_change=lambda _: self._apply_form(),
                    )

            with ui.card().classes("w-full"):
                ui.label("Data").classes("text-lg font-semibold")
                root_options = self.config_service.parquet_root_options()
                current_root = str(cfg.data.parquet_root)
                if current_root not in root_options:
                    root_options[current_root] = current_root
                with ui.grid(columns=2).classes("w-full gap-3"):
                    self.controls["data.parquet_root_choice"] = ui.select(
                        label="Prepared Data Root",
                        options=root_options,
                        value=current_root,
                        on_change=lambda _: self._on_parquet_root_choice_changed(),
                    ).classes("col-span-2")
                    with ui.row().classes("col-span-2 gap-2 items-center"):
                        ui.button("Use Available Period", on_click=lambda: self._detect_period_from_parquet_root()).props(
                            "outline"
                        )
                        self._period_hint_label = ui.label("").classes("text-sm text-slate-500")
                    self.controls["data.parquet_root"] = ui.input(
                        "Parquet Root",
                        value=str(cfg.data.parquet_root),
                        on_change=lambda _: self._on_parquet_root_input_changed(),
                    ).classes("col-span-2")
                    self.controls["data.timezone"] = ui.input(
                        "Timezone",
                        value=cfg.data.timezone,
                        on_change=lambda _: self._apply_form(),
                    )
                    self.controls["data.rth_start"] = ui.input(
                        "RTH Start",
                        value=cfg.data.rth_start,
                        on_change=lambda _: self._apply_form(),
                    )
                    self.controls["data.rth_end"] = ui.input(
                        "RTH End",
                        value=cfg.data.rth_end,
                        on_change=lambda _: self._apply_form(),
                    )
                    self.controls["data.lean_data_root"] = ui.input(
                        "LEAN Data Root",
                        value=str(cfg.data.lean_data_root),
                        on_change=lambda _: self._apply_form(),
                    )
                    self.controls["data.cache_root"] = ui.input(
                        "Cache Root",
                        value=str(cfg.data.cache_root),
                        on_change=lambda _: self._apply_form(),
                    )
                self._set_period_hint_from_root(current_root)

            with ui.card().classes("w-full"):
                ui.label("Universe").classes("text-lg font-semibold")
                with ui.grid(columns=2).classes("w-full gap-3"):
                    self.controls["universe.mode"] = ui.select(
                        label="Mode",
                        options={"custom_list": "Custom List", "all": "All"},
                        value=cfg.universe.mode,
                        on_change=lambda _: self._apply_form(),
                    )
                    self.controls["universe.tickers_csv"] = ui.input(
                        "Tickers CSV Path (optional)",
                        value=str(cfg.universe.tickers_csv or ""),
                        on_change=lambda _: self._apply_form(),
                    )
                    self.controls["universe.tickers"] = ui.input(
                        "Tickers (comma separated)",
                        value=", ".join(cfg.universe.tickers),
                        on_change=lambda _: self._apply_form(),
                    ).classes("col-span-2")
                    self.controls["universe.filters.min_price"] = ui.input(
                        "Min Price",
                        value="" if cfg.universe.filters.min_price is None else str(cfg.universe.filters.min_price),
                        on_change=lambda _: self._apply_form(),
                    )
                    self.controls["universe.filters.max_price"] = ui.input(
                        "Max Price",
                        value="" if cfg.universe.filters.max_price is None else str(cfg.universe.filters.max_price),
                        on_change=lambda _: self._apply_form(),
                    )
                    self.controls["universe.filters.min_avg_dollar_vol_20"] = ui.input(
                        "Min Avg Dollar Vol 20",
                        value=""
                        if cfg.universe.filters.min_avg_dollar_vol_20 is None
                        else str(cfg.universe.filters.min_avg_dollar_vol_20),
                        on_change=lambda _: self._apply_form(),
                    )
                    self.controls["universe.filters.exclude_otc"] = ui.switch(
                        "Exclude OTC",
                        value=cfg.universe.filters.exclude_otc,
                        on_change=lambda _: self._apply_form(),
                    )

            with ui.card().classes("w-full"):
                ui.label("Setups").classes("text-lg font-semibold")
                with ui.expansion("Common Breakout", icon="trending_up").classes("w-full"):
                    with ui.grid(columns=3).classes("w-full gap-3"):
                        cb = cfg.setups.common_breakout
                        self.controls["setups.common_breakout.enabled"] = ui.switch(
                            "Enabled",
                            value=cb.enabled,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.common_breakout.impulse_lookback_days"] = ui.number(
                            "Impulse Lookback Days",
                            value=cb.impulse_lookback_days,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.common_breakout.min_impulse_return_pct"] = ui.number(
                            "Min Impulse Return %",
                            value=cb.min_impulse_return_pct,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.common_breakout.consolidation_min_days"] = ui.number(
                            "Consolidation Min Days",
                            value=cb.consolidation_min_days,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.common_breakout.consolidation_max_days"] = ui.number(
                            "Consolidation Max Days",
                            value=cb.consolidation_max_days,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.common_breakout.max_consolidation_depth_pct"] = ui.number(
                            "Max Consolidation Depth %",
                            value=cb.max_consolidation_depth_pct,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.common_breakout.orh_window_minutes"] = ui.number(
                            "ORH Window Minutes",
                            value=cb.orh_window_minutes,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.common_breakout.entry_ladder_minutes"] = ui.input(
                            "Entry Ladder Minutes",
                            value=", ".join(str(v) for v in cb.entry_ladder_minutes),
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.common_breakout.stop_mode"] = ui.select(
                            label="Stop Mode",
                            options=[
                                "running_lod",
                                "running_lod_at_entry",
                                "dminus1_open",
                                "dminus1_low",
                            ],
                            value=cb.stop_mode,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.common_breakout.max_stop_multiple"] = ui.number(
                            "Max Stop Multiple",
                            value=cb.max_stop_multiple,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.common_breakout.partial_exit_day"] = ui.number(
                            "Partial Exit Day",
                            value=cb.partial_exit_day,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.common_breakout.partial_exit_fraction"] = ui.number(
                            "Partial Exit Fraction",
                            value=cb.partial_exit_fraction,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.common_breakout.trailing_ma_days"] = ui.number(
                            "Trailing MA Days",
                            value=cb.trailing_ma_days,
                            on_change=lambda _: self._apply_form(),
                        )

                with ui.expansion("Episodic Pivot", icon="bolt").classes("w-full"):
                    with ui.grid(columns=3).classes("w-full gap-3"):
                        ep = cfg.setups.episodic_pivot
                        self.controls["setups.episodic_pivot.enabled"] = ui.switch(
                            "Enabled",
                            value=ep.enabled,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.episodic_pivot.min_gap_pct"] = ui.number(
                            "Min Gap %",
                            value=ep.min_gap_pct,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.episodic_pivot.min_opening_volume_ratio"] = ui.number(
                            "Min Opening Volume Ratio",
                            value=ep.min_opening_volume_ratio,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.episodic_pivot.opening_volume_window_minutes"] = ui.number(
                            "Opening Volume Window Minutes",
                            value=ep.opening_volume_window_minutes,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.episodic_pivot.max_prior_runup_pct"] = ui.input(
                            "Max Prior Runup % (optional)",
                            value="" if ep.max_prior_runup_pct is None else str(ep.max_prior_runup_pct),
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.episodic_pivot.orh_window_minutes"] = ui.number(
                            "ORH Window Minutes",
                            value=ep.orh_window_minutes,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.episodic_pivot.entry_ladder_minutes"] = ui.input(
                            "Entry Ladder Minutes",
                            value=", ".join(str(v) for v in ep.entry_ladder_minutes),
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.episodic_pivot.stop_mode"] = ui.select(
                            label="Stop Mode",
                            options=[
                                "running_lod",
                                "running_lod_at_entry",
                                "dminus1_open",
                                "dminus1_low",
                            ],
                            value=ep.stop_mode,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.episodic_pivot.max_stop_multiple"] = ui.number(
                            "Max Stop Multiple",
                            value=ep.max_stop_multiple,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.episodic_pivot.max_stop_multiple_hard"] = ui.number(
                            "Max Stop Multiple Hard",
                            value=ep.max_stop_multiple_hard,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.episodic_pivot.trailing_ma_days"] = ui.number(
                            "Trailing MA Days",
                            value=ep.trailing_ma_days,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["setups.episodic_pivot.allow_scale_in"] = ui.switch(
                            "Allow Scale In",
                            value=ep.allow_scale_in,
                            on_change=lambda _: self._apply_form(),
                        )

            with ui.expansion("Advanced: Execution", icon="settings").classes("w-full"):
                with ui.card().classes("w-full"):
                    ex = cfg.execution
                    with ui.grid(columns=3).classes("w-full gap-3"):
                        self.controls["execution.initial_cash"] = ui.number(
                            "Initial Cash", value=ex.initial_cash, on_change=lambda _: self._apply_form()
                        )
                        self.controls["execution.max_positions"] = ui.number(
                            "Max Positions", value=ex.max_positions, on_change=lambda _: self._apply_form()
                        )
                        self.controls["execution.risk_per_trade"] = ui.number(
                            "Risk per Trade", value=ex.risk_per_trade, on_change=lambda _: self._apply_form()
                        )
                        self.controls["execution.commission_per_share"] = ui.number(
                            "Commission per Share",
                            value=ex.commission_per_share,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["execution.min_commission"] = ui.number(
                            "Min Commission", value=ex.min_commission, on_change=lambda _: self._apply_form()
                        )
                        self.controls["execution.slippage_bps"] = ui.number(
                            "Slippage (bps)", value=ex.slippage_bps, on_change=lambda _: self._apply_form()
                        )
                        self.controls["execution.entry_margin_buffer_pct"] = ui.number(
                            "Entry Margin Buffer %",
                            value=ex.entry_margin_buffer_pct,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["execution.max_order_error_rate"] = ui.number(
                            "Max Order Error Rate",
                            value=ex.max_order_error_rate,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["execution.max_holding_days"] = ui.input(
                            "Max Holding Days (optional)",
                            value="" if ex.max_holding_days is None else str(ex.max_holding_days),
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["execution.long_only"] = ui.switch(
                            "Long Only",
                            value=ex.long_only,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["execution.allow_overnight"] = ui.switch(
                            "Allow Overnight",
                            value=ex.allow_overnight,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["execution.force_flatten_on_end"] = ui.switch(
                            "Force Flatten on End",
                            value=ex.force_flatten_on_end,
                            on_change=lambda _: self._apply_form(),
                        )

            with ui.expansion("Infrastructure: Runner", icon="dns").classes("w-full"):
                with ui.card().classes("w-full"):
                    runner = cfg.runner
                    with ui.grid(columns=2).classes("w-full gap-3"):
                        self.controls["runner.mode"] = ui.select(
                            label="Runner Mode",
                            options=["auto", "docker", "dotnet"],
                            value=runner.mode,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["runner.docker_image"] = ui.input(
                            "Docker Image",
                            value=runner.docker_image,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["runner.algorithm_file"] = ui.input(
                            "Algorithm File",
                            value=runner.algorithm_file,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["runner.lean_repo_path"] = ui.input(
                            "LEAN Repo Path (optional)",
                            value="" if runner.lean_repo_path is None else str(runner.lean_repo_path),
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["runner.auto_convert_data"] = ui.switch(
                            "Auto Convert Data",
                            value=runner.auto_convert_data,
                            on_change=lambda _: self._apply_form(),
                        )
                        self.controls["runner.validate_lean_data"] = ui.switch(
                            "Validate LEAN Data",
                            value=runner.validate_lean_data,
                            on_change=lambda _: self._apply_form(),
                        )

    def _set_mode(self, mode: str) -> None:
        if self._form_container is None or self._yaml_container is None:
            return
        self._form_container.visible = mode == "form"
        self._yaml_container.visible = mode == "yaml"

    def _on_mode_changed(self) -> None:
        if self._mode_toggle is None:
            return
        mode = str(self._mode_toggle.value)
        if mode == "yaml" and self._yaml_editor is not None:
            self._yaml_editor.value = self.config_service.config_to_yaml(self.config)
        self._set_mode(mode)

    def _set_error(self, message: str) -> None:
        if self._error_label is not None:
            self._error_label.text = message

    def _set_period_hint_from_root(self, root_value: str) -> None:
        if self._period_hint_label is None:
            return
        detected = self.config_service.detect_period_for_parquet_root(Path(root_value))
        if detected is None:
            self._period_hint_label.text = "Detected available period: not found"
            return
        start_day, end_day = detected
        timeframe_label = ""
        for part in Path(root_value).parts:
            if part.startswith("timeframe="):
                timeframe = part.split("=", 1)[1]
                if timeframe != "1m":
                    timeframe_label = f" (timeframe={timeframe}; engine expects 1m for best fidelity)"
                break
        self._period_hint_label.text = f"Detected available period: {start_day} to {end_day}{timeframe_label}"

    def _on_parquet_root_choice_changed(self) -> None:
        selected = str(self.controls["data.parquet_root_choice"].value or "").strip()
        if not selected:
            return
        self.controls["data.parquet_root"].value = selected
        recommended_lean_root = self.config_service.lean_data_root_for_parquet_root(Path(selected))
        self.controls["data.lean_data_root"].value = str(recommended_lean_root)
        self._detect_period_from_parquet_root(notify=False)

    def _on_parquet_root_input_changed(self) -> None:
        root_text = str(self.controls["data.parquet_root"].value or "").strip()
        if root_text:
            choice_control = self.controls.get("data.parquet_root_choice")
            if choice_control is not None and root_text in choice_control.options:
                choice_control.value = root_text
                choice_control.update()
        self._set_period_hint_from_root(root_text)
        self._apply_form()

    def _detect_period_from_parquet_root(self, notify: bool = True) -> None:
        root_text = str(self.controls["data.parquet_root"].value or "").strip()
        if not root_text:
            if notify:
                ui.notify("Parquet Root is empty", type="warning")
            return
        detected = self.config_service.detect_period_for_parquet_root(Path(root_text))
        if detected is None:
            self._set_period_hint_from_root(root_text)
            if notify:
                ui.notify("Could not detect period from selected data root", type="warning")
            return
        start_day, end_day = detected
        self.controls["period.start_date"].value = str(start_day)
        self.controls["period.end_date"].value = str(end_day)
        self._set_period_hint_from_root(root_text)
        self._apply_form()
        if notify:
            ui.notify(f"Applied detected period: {start_day} to {end_day}", type="positive")

    def _reset_yaml(self) -> None:
        if self._yaml_editor is not None:
            self._yaml_editor.value = self.config_service.config_to_yaml(self.config)
        self._set_error("")

    def _apply_yaml(self) -> None:
        if self._yaml_editor is None:
            return
        try:
            config = self.config_service.yaml_to_config(self._yaml_editor.value)
        except Exception as exc:
            self._set_error(f"YAML validation failed: {exc}")
            return
        self.set_config(config)
        self._set_error("")
        ui.notify("YAML applied", type="positive")

    def _apply_form(self) -> None:
        try:
            candidate = self._build_candidate_dict()
            config = RunConfig.model_validate(candidate)
        except Exception as exc:
            self._set_error(f"Form validation failed: {exc}")
            return

        self.config = config
        if self._yaml_editor is not None:
            self._yaml_editor.value = self.config_service.config_to_yaml(config)
        self._set_error("")
        if self.on_change is not None:
            self.on_change(config)

    def _build_candidate_dict(self) -> dict[str, Any]:
        data = self.config.model_dump(mode="json")
        controls = self.controls

        data["run_name"] = str(controls["run_name"].value).strip() or "backtest_run"
        data["output"]["dir"] = str(controls["output_dir"].value).strip()

        data["period"]["start_date"] = date.fromisoformat(str(controls["period.start_date"].value).strip())
        data["period"]["end_date"] = date.fromisoformat(str(controls["period.end_date"].value).strip())

        data["data"]["parquet_root"] = str(controls["data.parquet_root"].value).strip()
        data["data"]["timezone"] = str(controls["data.timezone"].value).strip()
        data["data"]["rth_start"] = str(controls["data.rth_start"].value).strip()
        data["data"]["rth_end"] = str(controls["data.rth_end"].value).strip()
        data["data"]["lean_data_root"] = str(controls["data.lean_data_root"].value).strip()
        data["data"]["cache_root"] = str(controls["data.cache_root"].value).strip()

        data["universe"]["mode"] = str(controls["universe.mode"].value)
        tickers_csv = str(controls["universe.tickers_csv"].value).strip()
        data["universe"]["tickers_csv"] = tickers_csv if tickers_csv else None
        data["universe"]["tickers"] = _to_str_list(controls["universe.tickers"].value)
        data["universe"]["filters"]["min_price"] = _to_optional_float(controls["universe.filters.min_price"].value)
        data["universe"]["filters"]["max_price"] = _to_optional_float(controls["universe.filters.max_price"].value)
        data["universe"]["filters"]["min_avg_dollar_vol_20"] = _to_optional_float(
            controls["universe.filters.min_avg_dollar_vol_20"].value
        )
        data["universe"]["filters"]["exclude_otc"] = bool(controls["universe.filters.exclude_otc"].value)

        data["setups"]["common_breakout"]["enabled"] = bool(controls["setups.common_breakout.enabled"].value)
        data["setups"]["common_breakout"]["impulse_lookback_days"] = int(
            controls["setups.common_breakout.impulse_lookback_days"].value
        )
        data["setups"]["common_breakout"]["min_impulse_return_pct"] = float(
            controls["setups.common_breakout.min_impulse_return_pct"].value
        )
        data["setups"]["common_breakout"]["consolidation_min_days"] = int(
            controls["setups.common_breakout.consolidation_min_days"].value
        )
        data["setups"]["common_breakout"]["consolidation_max_days"] = int(
            controls["setups.common_breakout.consolidation_max_days"].value
        )
        data["setups"]["common_breakout"]["max_consolidation_depth_pct"] = float(
            controls["setups.common_breakout.max_consolidation_depth_pct"].value
        )
        data["setups"]["common_breakout"]["orh_window_minutes"] = int(
            controls["setups.common_breakout.orh_window_minutes"].value
        )
        data["setups"]["common_breakout"]["entry_ladder_minutes"] = _to_int_list(
            controls["setups.common_breakout.entry_ladder_minutes"].value
        )
        data["setups"]["common_breakout"]["stop_mode"] = str(controls["setups.common_breakout.stop_mode"].value)
        data["setups"]["common_breakout"]["max_stop_multiple"] = float(
            controls["setups.common_breakout.max_stop_multiple"].value
        )
        data["setups"]["common_breakout"]["partial_exit_day"] = int(
            controls["setups.common_breakout.partial_exit_day"].value
        )
        data["setups"]["common_breakout"]["partial_exit_fraction"] = float(
            controls["setups.common_breakout.partial_exit_fraction"].value
        )
        data["setups"]["common_breakout"]["trailing_ma_days"] = int(
            controls["setups.common_breakout.trailing_ma_days"].value
        )

        data["setups"]["episodic_pivot"]["enabled"] = bool(controls["setups.episodic_pivot.enabled"].value)
        data["setups"]["episodic_pivot"]["min_gap_pct"] = float(controls["setups.episodic_pivot.min_gap_pct"].value)
        data["setups"]["episodic_pivot"]["min_opening_volume_ratio"] = float(
            controls["setups.episodic_pivot.min_opening_volume_ratio"].value
        )
        data["setups"]["episodic_pivot"]["opening_volume_window_minutes"] = int(
            controls["setups.episodic_pivot.opening_volume_window_minutes"].value
        )
        data["setups"]["episodic_pivot"]["max_prior_runup_pct"] = _to_optional_float(
            controls["setups.episodic_pivot.max_prior_runup_pct"].value
        )
        data["setups"]["episodic_pivot"]["orh_window_minutes"] = int(
            controls["setups.episodic_pivot.orh_window_minutes"].value
        )
        data["setups"]["episodic_pivot"]["entry_ladder_minutes"] = _to_int_list(
            controls["setups.episodic_pivot.entry_ladder_minutes"].value
        )
        data["setups"]["episodic_pivot"]["stop_mode"] = str(controls["setups.episodic_pivot.stop_mode"].value)
        data["setups"]["episodic_pivot"]["max_stop_multiple"] = float(
            controls["setups.episodic_pivot.max_stop_multiple"].value
        )
        data["setups"]["episodic_pivot"]["max_stop_multiple_hard"] = float(
            controls["setups.episodic_pivot.max_stop_multiple_hard"].value
        )
        data["setups"]["episodic_pivot"]["trailing_ma_days"] = int(
            controls["setups.episodic_pivot.trailing_ma_days"].value
        )
        data["setups"]["episodic_pivot"]["allow_scale_in"] = bool(
            controls["setups.episodic_pivot.allow_scale_in"].value
        )

        data["execution"]["initial_cash"] = float(controls["execution.initial_cash"].value)
        data["execution"]["max_positions"] = int(controls["execution.max_positions"].value)
        data["execution"]["risk_per_trade"] = float(controls["execution.risk_per_trade"].value)
        data["execution"]["commission_per_share"] = float(controls["execution.commission_per_share"].value)
        data["execution"]["min_commission"] = float(controls["execution.min_commission"].value)
        data["execution"]["slippage_bps"] = float(controls["execution.slippage_bps"].value)
        data["execution"]["entry_margin_buffer_pct"] = float(controls["execution.entry_margin_buffer_pct"].value)
        data["execution"]["max_order_error_rate"] = float(controls["execution.max_order_error_rate"].value)
        data["execution"]["max_holding_days"] = _to_optional_int(controls["execution.max_holding_days"].value)
        data["execution"]["long_only"] = bool(controls["execution.long_only"].value)
        data["execution"]["allow_overnight"] = bool(controls["execution.allow_overnight"].value)
        data["execution"]["force_flatten_on_end"] = bool(controls["execution.force_flatten_on_end"].value)

        data["runner"]["mode"] = str(controls["runner.mode"].value)
        data["runner"]["docker_image"] = str(controls["runner.docker_image"].value).strip()
        data["runner"]["algorithm_file"] = str(controls["runner.algorithm_file"].value).strip()
        lean_repo_path = str(controls["runner.lean_repo_path"].value).strip()
        data["runner"]["lean_repo_path"] = lean_repo_path if lean_repo_path else None
        data["runner"]["auto_convert_data"] = bool(controls["runner.auto_convert_data"].value)
        data["runner"]["validate_lean_data"] = bool(controls["runner.validate_lean_data"].value)

        return data
