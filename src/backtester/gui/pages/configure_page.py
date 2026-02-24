"""Configure tab page."""

from __future__ import annotations

from nicegui import ui

from backtester.config import RunConfig
from backtester.experiment import experiment_id
from backtester.gui.components.config_form import ConfigForm
from backtester.gui.services.backtest_service import BacktestService
from backtester.gui.services.config_service import ConfigService
from backtester.gui.state import AppState


def render_configure_page(
    state: AppState,
    config_service: ConfigService,
    backtest_service: BacktestService,
) -> None:
    config = state.get_current_config() or config_service.build_default_config()
    state.set_current_config(config)

    with ui.column().classes("w-full max-w-[1200px] mx-auto gap-4 p-4"):
        ui.label("Configure").classes("text-3xl font-bold")
        ui.label("Set up a baseline run and launch the 5-stage LEAN pipeline.").classes("text-slate-500")

        docker_ok, docker_message = backtest_service.probe_docker()
        ui.badge(docker_message, color="positive" if docker_ok else "warning")

        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-end gap-3"):
                saved_select = ui.select(label="Saved Configs", options={}).classes("min-w-[320px]")
                save_name = ui.input("Save Name", value=config.run_name).classes("min-w-[220px]")
                run_name_input = ui.input("Quick Run Name", value=config.run_name).classes("min-w-[220px]")
                ui.space()

                def refresh_saved_options() -> None:
                    items = config_service.list_saved_configs()
                    saved_select.options = {
                        str(item["id"]): f"{item['name']} - {item['updated_at'][:19]}"
                        for item in items
                    }
                    saved_select.update()

                def on_load_config() -> None:
                    if not saved_select.value:
                        ui.notify("Pick a saved config first", type="warning")
                        return
                    try:
                        loaded = config_service.load_saved_config(int(saved_select.value))
                    except Exception as exc:
                        ui.notify(f"Load failed: {exc}", type="negative")
                        return
                    form.set_config(loaded)
                    state.set_current_config(loaded)
                    run_name_input.value = loaded.run_name
                    save_name.value = loaded.run_name
                    ui.notify("Config loaded", type="positive")

                def on_save_config() -> None:
                    candidate = form.get_config()
                    name = str(save_name.value or "").strip()
                    if not name:
                        ui.notify("Save name is required", type="warning")
                        return
                    try:
                        config_service.save_named_config(name, candidate)
                    except Exception as exc:
                        ui.notify(f"Save failed: {exc}", type="negative")
                        return
                    refresh_saved_options()
                    ui.notify("Config saved", type="positive")

                ui.button("Refresh", on_click=refresh_saved_options).props("flat")
                ui.button("Load", on_click=on_load_config).props("color=primary")
                ui.button("Save", on_click=on_save_config).props("color=secondary")

            refresh_saved_options()

        with ui.card().classes("w-full"):
            exp_badge = ui.badge(f"Experiment ID: {experiment_id(config)}", color="primary")

            def on_form_change(updated) -> None:
                state.set_current_config(updated)
                exp_badge.text = f"Experiment ID: {experiment_id(updated)}"

            form = ConfigForm(
                config_service=config_service,
                initial_config=config,
                on_change=on_form_change,
            )

        with ui.row().classes("w-full justify-end"):
            async def on_run() -> None:
                candidate = form.get_config()
                if run_name_input.value:
                    payload = candidate.model_dump(mode="json")
                    payload["run_name"] = str(run_name_input.value).strip() or candidate.run_name
                    try:
                        candidate = RunConfig.model_validate(payload)
                    except Exception as exc:
                        ui.notify(f"Run name invalid: {exc}", type="negative")
                        return
                state.set_current_config(candidate)
                try:
                    job_id = backtest_service.start_run(candidate)
                except Exception as exc:
                    ui.notify(f"Failed to start run: {exc}", type="negative")
                    return
                ui.notify("Backtest started", type="positive")
                ui.navigate.to(f"/results?job_id={job_id}")

            ui.button("Run Backtest", on_click=on_run).props("color=primary size=lg")
