"""NiceGUI application shell for BacktesterV3."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nicegui import app, ui

from backtester.gui.pages.compare_page import render_compare_page
from backtester.gui.pages.configure_page import render_configure_page
from backtester.gui.pages.results_page import render_results_page
from backtester.gui.pages.sweep_page import render_sweep_page
from backtester.gui.services.backtest_service import BacktestService
from backtester.gui.services.config_service import ConfigService
from backtester.gui.state import AppState, init_app_state


@dataclass(frozen=True)
class _GuiContext:
    state: AppState
    config_service: ConfigService
    backtest_service: BacktestService


_CTX: _GuiContext | None = None
_STATIC_MOUNTED = False
_ASSET_VERSION = "20260224g"


def _get_context(output_root: Path) -> _GuiContext:
    global _CTX
    if _CTX is not None:
        return _CTX

    state = init_app_state(output_root)
    config_service = ConfigService(state)
    if state.get_current_config() is None:
        state.set_current_config(config_service.build_default_config())
    backtest_service = BacktestService(state)
    _CTX = _GuiContext(
        state=state,
        config_service=config_service,
        backtest_service=backtest_service,
    )
    return _CTX


def _inject_theme() -> None:
    ui.add_head_html(
        f'<script src="/gui-static/vendor/lightweight-charts.standalone.production.js?v={_ASSET_VERSION}"></script>',
        shared=True,
    )
    ui.add_head_html(f'<script src="/gui-static/js/lwc_bridge.js?v={_ASSET_VERSION}"></script>', shared=True)
    ui.add_head_html(
        """
        <style>
            :root {
                --bt-bg: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);
                --bt-panel: rgba(255, 255, 255, 0.90);
                --bt-text: #0f172a;
            }
            body, .nicegui-content {
                font-family: "IBM Plex Sans", "Segoe UI", "Helvetica Neue", sans-serif;
                background: var(--bt-bg);
                color: var(--bt-text);
            }
            .q-card {
                background: var(--bt-panel);
                backdrop-filter: blur(3px);
                border: 1px solid rgba(148, 163, 184, 0.3);
                border-radius: 14px;
            }
        </style>
        """,
        shared=True,
    )


def _mount_static_assets() -> None:
    global _STATIC_MOUNTED
    if _STATIC_MOUNTED:
        return
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.add_static_files("/gui-static", static_dir)
    _STATIC_MOUNTED = True


def _top_nav(active: str) -> None:
    links = [
        ("Configure", "/"),
        ("Sweep", "/sweep"),
        ("Compare", "/compare"),
        ("Results", "/results"),
    ]
    with ui.header().classes("items-center justify-between px-4 py-2 bg-slate-900 text-white"):
        with ui.row().classes("items-center gap-3"):
            ui.label("BacktesterV3").classes("text-xl font-bold tracking-wide")
            ui.badge("Phase 3", color="primary")
        with ui.row().classes("items-center gap-1"):
            for label, route in links:
                props = "flat color=white"
                if route == active:
                    props = "unelevated color=primary"
                ui.button(label, on_click=lambda dest=route: ui.navigate.to(dest)).props(props)


def launch(port: int = 8050, output_root: str = "outputs", reload: bool = False) -> None:
    """Launch BacktesterV3 NiceGUI application."""
    ctx = _get_context(Path(output_root))
    _mount_static_assets()
    _inject_theme()

    @ui.page("/")
    def configure_page() -> None:
        _top_nav("/")
        render_configure_page(ctx.state, ctx.config_service, ctx.backtest_service)

    @ui.page("/sweep")
    def sweep_page() -> None:
        _top_nav("/sweep")
        render_sweep_page(ctx.state, ctx.backtest_service)

    @ui.page("/compare")
    def compare_page() -> None:
        _top_nav("/compare")
        render_compare_page(ctx.backtest_service)

    @ui.page("/results")
    def results_page() -> None:
        _top_nav("/results")
        render_results_page(ctx.state, ctx.backtest_service)

    ui.run(
        host="0.0.0.0",
        port=port,
        reload=reload,
        title="BacktesterV3",
        favicon="📈",
        show=False,
    )
