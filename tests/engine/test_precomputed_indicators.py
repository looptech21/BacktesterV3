from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from backtester.config import RunConfig
from backtester.engine.algorithm.QM_MVP import QM_MVP
from backtester.engine.strategies.common_breakout import _resolve_sma_value
from backtester.models import SetupIntent


def _make_config(parquet_root: Path, *, ep_gate_mode: str = "precomputed_preferred") -> RunConfig:
    return RunConfig.model_validate(
        {
            "run_name": "precomputed_indicators_test",
            "period": {"start_date": "2024-01-01", "end_date": "2024-01-31"},
            "data": {"parquet_root": str(parquet_root)},
            "universe": {"mode": "custom_list", "tickers": ["TSLA"]},
            "setups": {
                "episodic_pivot": {
                    "premarket_gate_source_mode": ep_gate_mode,
                }
            },
            "output": {"dir": "/tmp/backtester_v3_test"},
        }
    )


def _runtime_debug_seed() -> dict[str, int]:
    return {
        "precomputed_indicator_symbols_loaded": 0,
        "precomputed_indicator_rows_loaded": 0,
        "precomputed_indicator_hits": 0,
        "precomputed_indicator_misses": 0,
        "precomputed_indicator_load_errors": 0,
        "precomputed_premarket_symbols_loaded": 0,
        "precomputed_premarket_rows_loaded": 0,
        "precomputed_premarket_hits": 0,
        "precomputed_premarket_misses": 0,
        "precomputed_premarket_load_errors": 0,
        "ep_gate_precomputed_pass": 0,
        "ep_gate_precomputed_fail": 0,
        "ep_gate_proxy_fallback_pass": 0,
        "ep_gate_proxy_fallback_fail": 0,
    }


def _ep_intent(*, min_gap_pct: float = 10.0, min_opening_ratio: float = 3.0) -> SetupIntent:
    return SetupIntent(
        experiment_id="exp",
        symbol="TSLA",
        setup_type="episodic_pivot",
        source_signal_ts_utc=datetime(2024, 1, 2, tzinfo=timezone.utc),
        trade_day=date(2024, 1, 3),
        breakout_level=None,
        stop_level=95.0,
        metadata={
            "min_gap_pct": min_gap_pct,
            "min_opening_volume_ratio": min_opening_ratio,
            "opening_volume_window_minutes": 20,
        },
    )


def test_resolve_sma_value_prefers_precomputed() -> None:
    closes = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])

    value, source = _resolve_sma_value(closes, 3, {"sma_3": 9.5})

    assert value == 9.5
    assert source == "precomputed"


def test_resolve_sma_value_falls_back_to_rolling() -> None:
    closes = pd.Series([10.0, 20.0, 30.0, 40.0])

    value, source = _resolve_sma_value(closes, 3, None)

    assert value == 30.0
    assert source == "rolling"


def test_load_precomputed_daily_indicators_from_dataset_root(tmp_path: Path) -> None:
    dataset_root = tmp_path / "dataset"
    bars_root = dataset_root / "bars" / "5m"
    bars_root.mkdir(parents=True)

    indicators_dir = dataset_root / "indicators_daily" / "symbol=TSLA"
    indicators_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "symbol": ["TSLA", "TSLA"],
            "session_date": ["2024-01-02", "2024-01-03"],
            "sma_10": [100.0, 101.0],
            "sma_20": [98.0, 99.0],
            "ema_10": [100.5, 101.5],
        }
    ).to_parquet(indicators_dir / "year=2024.parquet", index=False)

    algo = QM_MVP.__new__(QM_MVP)
    algo.run_config = _make_config(bars_root)
    algo.tickers = ["TSLA"]
    algo.runtime_debug = _runtime_debug_seed()
    algo.precomputed_daily_indicators = {}

    algo._load_precomputed_daily_indicators()

    loaded = algo._precomputed_indicator_row("TSLA", date(2024, 1, 3))
    missing = algo._precomputed_indicator_row("TSLA", date(2024, 1, 4))

    assert loaded is not None
    assert loaded["sma_10"] == 101.0
    assert loaded["sma_20"] == 99.0
    assert algo.runtime_debug["precomputed_indicator_symbols_loaded"] == 1
    assert algo.runtime_debug["precomputed_indicator_rows_loaded"] == 2
    assert algo.runtime_debug["precomputed_indicator_hits"] == 1
    assert missing is None
    assert algo.runtime_debug["precomputed_indicator_misses"] == 1


def test_load_precomputed_premarket_from_dataset_root(tmp_path: Path) -> None:
    dataset_root = tmp_path / "dataset"
    bars_root = dataset_root / "bars" / "5m"
    bars_root.mkdir(parents=True)

    features_dir = dataset_root / "features_premarket" / "symbol=TSLA"
    features_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "symbol": ["TSLA", "TSLA"],
            "session_date": ["2024-01-02", "2024-01-03"],
            "gap_vs_prev_close": [0.08, 0.12],
            "rel_premarket_volume_20d": [2.0, 3.5],
        }
    ).to_parquet(features_dir / "year=2024.parquet", index=False)

    algo = QM_MVP.__new__(QM_MVP)
    algo.run_config = _make_config(bars_root)
    algo.tickers = ["TSLA"]
    algo.runtime_debug = _runtime_debug_seed()
    algo.precomputed_premarket_features = {}

    algo._load_precomputed_premarket_features()

    loaded = algo._precomputed_premarket_row("TSLA", date(2024, 1, 3))
    missing = algo._precomputed_premarket_row("TSLA", date(2024, 1, 4))

    assert loaded is not None
    assert loaded["gap_vs_prev_close"] == 0.12
    assert loaded["rel_premarket_volume_20d"] == 3.5
    assert algo.runtime_debug["precomputed_premarket_symbols_loaded"] == 1
    assert algo.runtime_debug["precomputed_premarket_rows_loaded"] == 2
    assert algo.runtime_debug["precomputed_premarket_hits"] == 1
    assert missing is None
    assert algo.runtime_debug["precomputed_premarket_misses"] == 1


def test_ep_activation_prefers_precomputed_source() -> None:
    algo = QM_MVP.__new__(QM_MVP)
    algo.runtime_debug = _runtime_debug_seed()
    algo.precomputed_premarket_features = {
        "TSLA": {
            date(2024, 1, 3): {
                "gap_vs_prev_close": 0.15,
                "rel_premarket_volume_20d": 4.0,
            }
        }
    }

    def _proxy_should_not_run(**_: object) -> dict[str, object]:
        raise AssertionError("proxy fallback should not be used when precomputed data is complete")

    algo._evaluate_ep_activation_proxy = _proxy_should_not_run  # type: ignore[attr-defined]

    result = algo._evaluate_ep_activation(
        ticker="TSLA",
        intent=_ep_intent(min_gap_pct=10.0, min_opening_ratio=3.0),
        now_est=datetime(2024, 1, 3, 9, 35),
        bar=object(),
    )

    assert result["status"] == "pass"
    evidence = result.get("evidence", {})
    assert evidence.get("gate_source") == "precomputed"
    assert algo.runtime_debug["ep_gate_precomputed_pass"] == 1
    assert algo.runtime_debug["ep_gate_proxy_fallback_pass"] == 0


def test_ep_activation_falls_back_to_proxy_when_precomputed_incomplete() -> None:
    algo = QM_MVP.__new__(QM_MVP)
    algo.runtime_debug = _runtime_debug_seed()
    algo.precomputed_premarket_features = {
        "TSLA": {
            date(2024, 1, 3): {
                "gap_vs_prev_close": 0.15,
            }
        }
    }

    def _proxy_pass(**_: object) -> dict[str, object]:
        return {
            "status": "pass",
            "evidence": {
                "gate_source": "proxy_fallback",
                "gap_pct": 15.0,
                "opening_volume_ratio": 3.2,
            },
        }

    algo._evaluate_ep_activation_proxy = _proxy_pass  # type: ignore[attr-defined]

    result = algo._evaluate_ep_activation(
        ticker="TSLA",
        intent=_ep_intent(min_gap_pct=10.0, min_opening_ratio=3.0),
        now_est=datetime(2024, 1, 3, 9, 35),
        bar=object(),
    )

    assert result["status"] == "pass"
    evidence = result.get("evidence", {})
    assert evidence.get("gate_source") == "proxy_fallback"
    assert algo.runtime_debug["ep_gate_precomputed_pass"] == 0
    assert algo.runtime_debug["ep_gate_proxy_fallback_pass"] == 1


def test_ep_activation_proxy_only_ignores_precomputed() -> None:
    algo = QM_MVP.__new__(QM_MVP)
    algo.run_config = _make_config(Path('/tmp/dataset/bars/5m'), ep_gate_mode='proxy_only')
    algo.runtime_debug = _runtime_debug_seed()
    algo.precomputed_premarket_features = {
        'TSLA': {
            date(2024, 1, 3): {
                'gap_vs_prev_close': 0.15,
                'rel_premarket_volume_20d': 4.0,
            }
        }
    }

    def _proxy_pass(**_: object) -> dict[str, object]:
        return {
            'status': 'pass',
            'evidence': {
                'gate_source': 'proxy_fallback',
                'gap_pct': 15.0,
                'opening_volume_ratio': 3.2,
            },
        }

    algo._evaluate_ep_activation_proxy = _proxy_pass  # type: ignore[attr-defined]

    result = algo._evaluate_ep_activation(
        ticker='TSLA',
        intent=_ep_intent(min_gap_pct=10.0, min_opening_ratio=3.0),
        now_est=datetime(2024, 1, 3, 9, 35),
        bar=object(),
    )

    assert result['status'] == 'pass'
    evidence = result.get('evidence', {})
    assert evidence.get('gate_source') == 'proxy_fallback'
    assert algo.runtime_debug['ep_gate_precomputed_pass'] == 0
    assert algo.runtime_debug['ep_gate_proxy_fallback_pass'] == 1



def test_ep_activation_require_precomputed_missing_rejects_without_proxy() -> None:
    algo = QM_MVP.__new__(QM_MVP)
    algo.run_config = _make_config(Path('/tmp/dataset/bars/5m'), ep_gate_mode='require_precomputed')
    algo.runtime_debug = _runtime_debug_seed()
    algo.precomputed_premarket_features = {}

    def _proxy_should_not_run(**_: object) -> dict[str, object]:
        raise AssertionError('proxy should not run in require_precomputed mode when row is missing')

    algo._evaluate_ep_activation_proxy = _proxy_should_not_run  # type: ignore[attr-defined]

    result = algo._evaluate_ep_activation(
        ticker='TSLA',
        intent=_ep_intent(min_gap_pct=10.0, min_opening_ratio=3.0),
        now_est=datetime(2024, 1, 3, 9, 35),
        bar=object(),
    )

    assert result['status'] == 'fail'
    assert result.get('code') == 'EP_PRECOMPUTED_MISSING'
    evidence = result.get('evidence', {})
    assert evidence.get('gate_mode') == 'require_precomputed'
    assert algo.runtime_debug['ep_gate_precomputed_fail'] == 1



def test_ep_activation_require_precomputed_incomplete_rejects_without_proxy() -> None:
    algo = QM_MVP.__new__(QM_MVP)
    algo.run_config = _make_config(Path('/tmp/dataset/bars/5m'), ep_gate_mode='require_precomputed')
    algo.runtime_debug = _runtime_debug_seed()
    algo.precomputed_premarket_features = {
        'TSLA': {
            date(2024, 1, 3): {
                'gap_vs_prev_close': 0.15,
            }
        }
    }

    def _proxy_should_not_run(**_: object) -> dict[str, object]:
        raise AssertionError('proxy should not run in require_precomputed mode when row is incomplete')

    algo._evaluate_ep_activation_proxy = _proxy_should_not_run  # type: ignore[attr-defined]

    result = algo._evaluate_ep_activation(
        ticker='TSLA',
        intent=_ep_intent(min_gap_pct=10.0, min_opening_ratio=3.0),
        now_est=datetime(2024, 1, 3, 9, 35),
        bar=object(),
    )

    assert result['status'] == 'fail'
    assert result.get('code') == 'EP_PRECOMPUTED_INCOMPLETE'
    evidence = result.get('evidence', {})
    assert evidence.get('gate_mode') == 'require_precomputed'
    assert algo.runtime_debug['ep_gate_precomputed_fail'] == 1

