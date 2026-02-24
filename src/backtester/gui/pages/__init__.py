"""GUI page exports."""

from backtester.gui.pages.compare_page import render_compare_page
from backtester.gui.pages.configure_page import render_configure_page
from backtester.gui.pages.results_page import render_results_page
from backtester.gui.pages.sweep_page import render_sweep_page

__all__ = [
    "render_compare_page",
    "render_configure_page",
    "render_results_page",
    "render_sweep_page",
]
