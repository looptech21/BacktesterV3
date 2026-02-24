from __future__ import annotations

import backtester.gui.components.lightweight_chart as chart_module
from backtester.gui.components.lightweight_chart import LightweightCandleChart


def test_lightweight_chart_render_dispatches_bridge_call(monkeypatch):
    calls: list[str] = []

    def fake_run_javascript(code: str, *, timeout: float = 1.0):  # type: ignore[no-untyped-def]
        calls.append(code)

    monkeypatch.setattr(chart_module.ui, "run_javascript", fake_run_javascript)

    chart = LightweightCandleChart(container_id=42)
    chart.render(
        bars=[{"time": 1577975400, "open": 100.0, "high": 102.0, "low": 99.5, "close": 101.25}],
        markers=[
            {
                "time": 1577975460,
                "price": 101.25,
                "logical": 0.4,
                "shape": "arrowUp",
                "color": "#16a34a",
                "text": "Entry",
            }
        ],
        title="TSLA · full/5m",
        empty_message="No data",
    )

    assert calls
    assert "BacktesterLWC.render" in calls[-1]
    assert '"containerId":42' in calls[-1]
    assert '"title":"TSLA · full/5m"' in calls[-1]
    assert '"logical":0.4' in calls[-1]


def test_lightweight_chart_clear_dispatches_bridge_call(monkeypatch):
    calls: list[str] = []

    def fake_run_javascript(code: str, *, timeout: float = 1.0):  # type: ignore[no-untyped-def]
        calls.append(code)

    monkeypatch.setattr(chart_module.ui, "run_javascript", fake_run_javascript)

    chart = LightweightCandleChart(container_id=77)
    chart.clear("No bars")

    assert calls
    assert "BacktesterLWC.clear" in calls[-1]
    assert '"containerId":77' in calls[-1]
    assert '"message":"No bars"' in calls[-1]
