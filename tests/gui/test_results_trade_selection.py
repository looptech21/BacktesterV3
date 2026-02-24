from __future__ import annotations

from backtester.gui.pages.results_page import get_trade_by_row_id, pick_selected_trade_id, trade_row_id


def test_pick_selected_trade_id_prefers_previous_selection_when_present():
    trades = [
        {"trade_id": "T0001", "symbol": "AAPL"},
        {"trade_id": "T0002", "symbol": "TSLA"},
    ]

    assert pick_selected_trade_id(trades, "T0002") == "T0002"
    assert pick_selected_trade_id(trades, "MISSING") == "T0001"
    assert pick_selected_trade_id([], "T0002") is None


def test_get_trade_by_row_id_handles_explicit_and_generated_ids():
    trades = [
        {"trade_id": "T0001", "symbol": "AAPL"},
        {"trade_id": "", "symbol": "TSLA"},
    ]

    assert trade_row_id(trades[0], 0) == "T0001"
    assert trade_row_id(trades[1], 1) == "trade_1"
    assert get_trade_by_row_id(trades, "T0001") == trades[0]
    assert get_trade_by_row_id(trades, "trade_1") == trades[1]
    assert get_trade_by_row_id(trades, "nope") is None
