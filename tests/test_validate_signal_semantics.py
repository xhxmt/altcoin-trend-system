import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_ultra_signal_production.py"
_SPEC = importlib.util.spec_from_file_location("validate_ultra_signal_production_semantics", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


def test_parse_signal_selector_normalizes_family_and_grade():
    selector = _MODULE.parse_signal_selector("ignition_A")

    assert selector.family.name == "ignition"
    assert selector.grade == "A"
    assert selector.label == "ignition_A"


def test_parse_signal_selector_supports_ignition_extreme_compatibility():
    selector = _MODULE.parse_signal_selector("ignition_EXTREME")

    assert selector.family.name == "ignition"
    assert selector.grade == "EXTREME"
    assert selector.label == "ignition_EXTREME"


def test_parse_signal_selector_rejects_unsupported_grade():
    with pytest.raises(ValueError, match="unsupported signal selector"):
        _MODULE.parse_signal_selector("ultra_high_conviction_A")


def test_signal_family_slug_preserves_ultra_compatibility():
    selector = _MODULE.parse_signal_selector("ultra_high_conviction")

    assert _MODULE._signal_family_slug("ultra_high_conviction") == "ultra"
    assert _MODULE._signal_family_slug(selector) == "ultra"


def test_required_features_preserves_ultra_validation_metadata_contract():
    required_features = _MODULE._required_features("ultra_high_conviction")

    assert "return_1h_pct" in required_features
    assert "return_24h_rank" in required_features
    assert "volume_ratio_24h" in required_features
    assert "quality_score" in required_features
    assert "breakout_20d" in required_features
    assert "ultra_high_conviction" in required_features
    assert required_features != list(_MODULE.SIGNAL_FAMILY_REGISTRY["ultra_high_conviction"].required_columns)


def test_select_signal_rows_uses_registry_columns():
    frame = pd.DataFrame(
        [
            {
                "exchange": "binance",
                "symbol": "AAAUSDT",
                "ts": pd.Timestamp("2026-04-24T10:00:00Z"),
                "signal_v2_ignition_candidate": True,
                "signal_v2_ignition_grade": "A",
                "signal_v2_reacceleration_candidate": False,
                "signal_v2_reacceleration_grade": "",
                "signal_v2_continuation_candidate": False,
                "signal_v2_continuation_grade": "",
                "ultra_high_conviction": False,
            },
            {
                "exchange": "binance",
                "symbol": "BBBUSDT",
                "ts": pd.Timestamp("2026-04-24T10:00:00Z"),
                "signal_v2_ignition_candidate": True,
                "signal_v2_ignition_grade": "B",
                "signal_v2_reacceleration_candidate": False,
                "signal_v2_reacceleration_grade": "",
                "signal_v2_continuation_candidate": False,
                "signal_v2_continuation_grade": "",
                "ultra_high_conviction": False,
            },
        ]
    )

    selected = _MODULE._select_signal_rows(frame, _MODULE.parse_signal_selector("ignition_A"))

    assert selected["symbol"].tolist() == ["AAAUSDT"]
    assert selected["signal_family"].tolist() == ["ignition"]
    assert selected["signal_grade"].tolist() == ["A"]


def test_select_signal_rows_derives_registry_columns_from_production_grade_columns():
    frame = pd.DataFrame(
        [
            {
                "exchange": "binance",
                "symbol": "AAAUSDT",
                "ts": pd.Timestamp("2026-04-24T10:00:00Z"),
                "ignition_grade": "A",
            },
            {
                "exchange": "binance",
                "symbol": "BBBUSDT",
                "ts": pd.Timestamp("2026-04-24T10:00:00Z"),
                "ignition_grade": "",
            },
            {
                "exchange": "binance",
                "symbol": "CCCUSDT",
                "ts": pd.Timestamp("2026-04-24T10:00:00Z"),
                "ignition_grade": "B",
            },
        ]
    )

    selected = _MODULE._select_signal_rows(frame, _MODULE.parse_signal_selector("ignition_A"))

    assert selected["symbol"].tolist() == ["AAAUSDT"]
    assert selected["signal_family"].tolist() == ["ignition"]
    assert selected["signal_grade"].tolist() == ["A"]
    assert selected["signal_selector"].tolist() == ["ignition_A"]


def test_select_signal_rows_missing_selector_column_is_hard_error():
    frame = pd.DataFrame([{"exchange": "binance", "symbol": "AAAUSDT"}])

    with pytest.raises(ValueError, match="missing required columns"):
        _MODULE._select_signal_rows(frame, _MODULE.parse_signal_selector("continuation"))


def test_hour_bucket_start_and_signal_available_at():
    ts = pd.Timestamp("2026-04-22T10:37:15Z")

    assert _MODULE.hour_bucket_start(ts).isoformat() == "2026-04-22T10:00:00+00:00"
    assert _MODULE.signal_available_at(pd.Timestamp("2026-04-22T10:00:00Z")).isoformat() == "2026-04-22T11:00:00+00:00"


def test_default_validation_window_ends_24h_before_run_time():
    now = datetime(2026, 4, 25, 10, 34, 22, tzinfo=timezone.utc)

    start, end = _MODULE.default_validation_window(30, now=now)

    assert start.isoformat() == "2026-03-25T10:00:00+00:00"
    assert end.isoformat() == "2026-04-24T10:00:00+00:00"


def test_default_validation_window_rejects_invalid_days():
    with pytest.raises(ValueError, match="window_days must be >= 1"):
        _MODULE.default_validation_window(0, now=datetime(2026, 4, 25, tzinfo=timezone.utc))


def test_fetch_forward_rows_uses_available_at_inclusive(monkeypatch):
    captured = {}

    class FakeResult:
        def mappings(self):
            return self

        def all(self):
            return []

    class FakeConnection:
        def execute(self, statement, params):
            captured["sql"] = str(statement)
            captured["params"] = params
            return FakeResult()

    class FakeBegin:
        def __enter__(self):
            return FakeConnection()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeEngine:
        def begin(self):
            return FakeBegin()

    signal_ts = pd.Timestamp("2026-04-22T10:00:00Z").to_pydatetime()
    _MODULE.fetch_forward_1m_rows(FakeEngine(), 123, signal_ts, pd.Timedelta(hours=24))

    assert "m.ts >= :signal_available_at" in captured["sql"]
    assert "m.ts < :horizon_end" in captured["sql"]
    assert captured["params"]["signal_available_at"].isoformat() == "2026-04-22T11:00:00+00:00"
    assert captured["params"]["horizon_end"].isoformat() == "2026-04-23T11:00:00+00:00"


def test_evaluate_signal_family_scans_forward_from_signal_availability(monkeypatch):
    signal_ts = pd.Timestamp("2026-04-22T10:00:00Z")
    available_ts = pd.Timestamp("2026-04-22T11:00:00Z")
    feature_frame = pd.DataFrame(
        [
            {
                "asset_id": 101,
                "exchange": "binance",
                "symbol": "LEAKUSDT",
                "ts": signal_ts,
                "close": 100.0,
                "ultra_high_conviction": True,
                "return_1h_pct": 12.0,
                "return_4h_pct": 30.0,
                "return_7d_pct": 80.0,
                "return_24h_pct": 55.0,
                "return_30d_pct": 90.0,
                "volume_ratio_24h": 5.0,
                "return_24h_rank": 1,
                "return_24h_percentile": 0.99,
                "return_7d_percentile": 0.99,
                "return_30d_percentile": 0.9,
                "quality_score": 95.0,
                "breakout_20d": True,
                "risk_flags": [],
            }
        ]
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(_MODULE, "fetch_hourly_bars", lambda *args, **kwargs: pd.DataFrame([{"hourly": True}]))
    monkeypatch.setattr(_MODULE, "_prepare_feature_frame", lambda hourly: feature_frame)

    def fake_fetch_forward_1m_rows(engine, asset_id, signal_ts_arg, horizon):
        captured["fetch_asset_id"] = asset_id
        captured["fetch_signal_ts"] = pd.Timestamp(signal_ts_arg)
        captured["fetch_horizon"] = horizon
        return pd.DataFrame([{"ts": available_ts, "high": 111.0, "low": 99.0}])

    def fake_compute_validation_path_labels(*, signal_ts, entry_price, future_rows):
        captured["label_signal_ts"] = pd.Timestamp(signal_ts)
        return {
            "signal_ts": signal_ts.isoformat(),
            "signal_available_at": available_ts.isoformat(),
            "entry_ts": available_ts.isoformat(),
            "entry_price": entry_price,
            "entry_policy": _MODULE.ENTRY_POLICY,
            "label_complete_1h": False,
            "label_complete_4h": False,
            "label_complete_24h": False,
            "expected_minutes_1h": 60,
            "expected_minutes_4h": 240,
            "expected_minutes_24h": 1440,
            "missing_minutes_1h": 59,
            "missing_minutes_4h": 239,
            "missing_minutes_24h": 1439,
            "mfe_1h_pct": 11.0,
            "mfe_4h_pct": 11.0,
            "mfe_24h_pct": 11.0,
            "mae_1h_pct": 0.0,
            "mae_4h_pct": 0.0,
            "mae_24h_pct": 0.0,
            "abs_mae_1h_pct": 1.0,
            "abs_mae_4h_pct": 2.0,
            "abs_mae_24h_pct": 0.0,
            "mfe_before_dd8_pct": 11.0,
            "mae_before_hit_10pct": 0.0,
            "mae_after_hit_10pct": 0.0,
            "hit_10pct_1h": True,
            "hit_10pct_4h": True,
            "hit_10pct_24h": True,
            "hit_10pct_before_drawdown_8pct": True,
            "hit_10_before_dd8": True,
            "hit_10pct_first": True,
            "drawdown_8pct_first": False,
            "time_to_hit_10pct_minutes": 0.0,
            "time_to_drawdown_8pct_minutes": None,
            "path_order": "target_first",
            "ambiguous_same_bar": False,
            "path_results": {"target_10_dd_8": {"hit": True}},
            "next_minute_open_entry_price": None,
            "next_minute_open_entry_return_delta_pct": None,
        }

    monkeypatch.setattr(_MODULE, "fetch_forward_1m_rows", fake_fetch_forward_1m_rows)
    monkeypatch.setattr(_MODULE, "compute_validation_path_labels", fake_compute_validation_path_labels)

    _, rows = _MODULE.evaluate_signal_family(
        object(),
        "binance",
        datetime(2026, 4, 22, 10, tzinfo=timezone.utc),
        datetime(2026, 4, 22, 11, tzinfo=timezone.utc),
        signal_family="ultra_high_conviction",
    )

    assert rows[0]["ts"] == "2026-04-22T10:00:00+00:00"
    assert captured["fetch_signal_ts"].isoformat() == "2026-04-22T10:00:00+00:00"
    assert captured["label_signal_ts"].isoformat() == "2026-04-22T10:00:00+00:00"
    assert rows[0]["signal_available_at"] == "2026-04-22T11:00:00+00:00"
    assert rows[0]["entry_ts"] == "2026-04-22T11:00:00+00:00"
    assert rows[0]["path_results_json"] == '{"target_10_dd_8": {"hit": true}}'
    assert rows[0]["expected_minutes_1h"] == 60
    assert rows[0]["expected_minutes_4h"] == 240
    assert rows[0]["expected_minutes_24h"] == 1440
    assert rows[0]["missing_minutes_1h"] == 59
    assert rows[0]["missing_minutes_4h"] == 239
    assert rows[0]["missing_minutes_24h"] == 1439
    assert rows[0]["abs_mae_1h_pct"] == 1.0
    assert rows[0]["abs_mae_4h_pct"] == 2.0
    assert rows[0]["abs_mae_24h_pct"] == 0.0


def test_validation_path_labels_include_first_availability_minute_and_exclude_signal_bar():
    future_rows = pd.DataFrame(
        [
            {"ts": pd.Timestamp("2026-04-22T10:59:00Z"), "high": 111.0, "low": 91.0},
            {"ts": pd.Timestamp("2026-04-22T11:00:00Z"), "high": 111.0, "low": 100.0},
        ]
    )

    labels = _MODULE.compute_validation_path_labels(
        signal_ts=pd.Timestamp("2026-04-22T10:00:00Z"),
        entry_price=100.0,
        future_rows=future_rows,
    )

    assert labels["hit_10pct_before_drawdown_8pct"] is True
    assert labels["time_to_hit_10pct_minutes"] == 0.0
    assert labels["drawdown_8pct_first"] is False


def test_forward_rows_start_at_signal_available_at_inclusive():
    rows = pd.DataFrame(
        [
            {"ts": "2026-04-22T10:59:00Z", "open": 100.0, "high": 200.0, "low": 50.0},
            {"ts": "2026-04-22T11:00:00Z", "open": 101.0, "high": 106.0, "low": 99.0},
            {"ts": "2026-04-22T11:01:00Z", "open": 106.0, "high": 112.0, "low": 105.0},
        ]
    )

    labels = _MODULE.compute_validation_path_labels(
        signal_ts=pd.Timestamp("2026-04-22T10:00:00Z"),
        entry_price=100.0,
        future_rows=rows,
        horizons=(pd.Timedelta(hours=1),),
    )

    assert labels["entry_ts"] == "2026-04-22T11:00:00+00:00"
    assert labels["label_complete_1h"] is False
    assert labels["mfe_1h_pct"] == 12.0
    assert labels["mae_1h_pct"] == -1.0
    assert labels["abs_mae_1h_pct"] == 1.0
    assert labels["hit_10pct_1h"] is True
    assert labels["time_to_hit_10pct_minutes"] == 1.0


def test_same_bar_target_drawdown_is_conservative_drawdown_first():
    rows = pd.DataFrame(
        [
            {"ts": "2026-04-22T11:00:00Z", "open": 100.0, "high": 111.0, "low": 91.0},
        ]
    )

    labels = _MODULE.compute_validation_path_labels(
        signal_ts=pd.Timestamp("2026-04-22T10:00:00Z"),
        entry_price=100.0,
        future_rows=rows,
        horizons=(pd.Timedelta(hours=1),),
    )

    assert labels["hit_10pct_before_drawdown_8pct"] is False
    assert labels["path_order"] == "ambiguous_same_bar"
    assert labels["ambiguous_same_bar"] is True


def test_sensitivity_matrix_cell_has_denominator_and_incomplete_count():
    evaluated = [
        {"label_complete_24h": True, "path_results": {"target_5_dd_5": {"hit": True}}},
        {"label_complete_24h": True, "path_results": {"target_5_dd_5": {"hit": False}}},
        {"label_complete_24h": False, "path_results": {"target_5_dd_5": {"hit": False}}},
    ]

    matrix = _MODULE.build_sensitivity_matrix(evaluated)

    assert matrix["target_5_dd_5"] == {
        "eligible_count": 2,
        "hit_count": 1,
        "incomplete_count": 1,
        "precision": 0.5,
    }


def test_summary_and_sensitivity_matrix_default_legacy_missing_labels_to_complete():
    row = {
        "hit_10pct_1h": True,
        "hit_10pct_4h": True,
        "hit_10pct_24h": True,
        "hit_10pct_before_drawdown_8pct": True,
        "hit_10pct_first": True,
        "drawdown_8pct_first": False,
        "ambiguous_same_bar": False,
        "mfe_24h_pct": 12.0,
        "mae_24h_pct": -2.0,
        "abs_mae_24h_pct": 2.0,
        "time_to_hit_10pct_minutes": 8.0,
        "path_results": {"target_10_dd_8": {"hit": True}},
    }

    summary = _MODULE.summarize_evaluated_signals([row])
    matrix = _MODULE.build_sensitivity_matrix([row])

    assert summary["primary_label_complete_count"] == 1
    assert summary["incomplete_label_count"] == 0
    assert matrix["target_10_dd_8"] == {
        "eligible_count": 1,
        "hit_count": 1,
        "incomplete_count": 0,
        "precision": 1.0,
    }


def test_compute_validation_path_labels_handles_empty_forward_rows():
    labels = _MODULE.compute_validation_path_labels(
        signal_ts=pd.Timestamp("2026-04-22T10:00:00Z"),
        entry_price=100.0,
        future_rows=pd.DataFrame(),
    )

    assert labels["label_complete_24h"] is False
    assert labels["missing_minutes_24h"] == 1440
    assert labels["mfe_24h_pct"] == 0.0
    assert labels["mae_24h_pct"] == 0.0
    assert labels["abs_mae_24h_pct"] == 0.0
    assert labels["path_order"] == "unresolved"
    assert labels["hit_10_before_dd8"] is False


@pytest.mark.parametrize("entry_price", [0.0, -100.0, float("nan")])
def test_compute_validation_path_labels_handles_invalid_entry_price_conservatively(entry_price):
    rows = pd.DataFrame(
        [
            {"ts": "2026-04-22T11:00:00Z", "open": 100.0, "high": 1000.0, "low": 1.0},
        ]
    )

    labels = _MODULE.compute_validation_path_labels(
        signal_ts=pd.Timestamp("2026-04-22T10:00:00Z"),
        entry_price=entry_price,
        future_rows=rows,
    )

    assert labels["invalid_entry_price"] is True
    assert labels["label_error"] == "invalid_entry_price"
    assert labels["entry_price"] is None
    assert labels["label_complete_1h"] is False
    assert labels["label_complete_4h"] is False
    assert labels["label_complete_24h"] is False
    assert labels["missing_minutes_1h"] == 60
    assert labels["missing_minutes_4h"] == 240
    assert labels["missing_minutes_24h"] == 1440
    assert labels["mfe_24h_pct"] == 0.0
    assert labels["mae_24h_pct"] == 0.0
    assert labels["abs_mae_24h_pct"] == 0.0
    assert labels["path_order"] == "unresolved"
    assert labels["hit_10_before_dd8"] is False
    assert labels["hit_10pct_before_drawdown_8pct"] is False
    assert labels["path_results"]["target_10_dd_8"]["hit"] is False


def test_summarize_evaluated_signals_counts_v11_unresolved_path_order():
    summary = _MODULE.summarize_evaluated_signals(
        [
            {
                "label_complete_1h": True,
                "label_complete_4h": True,
                "label_complete_24h": True,
                "hit_10pct_1h": False,
                "hit_10pct_4h": False,
                "hit_10pct_24h": False,
                "hit_10pct_before_drawdown_8pct": False,
                "hit_10pct_first": False,
                "drawdown_8pct_first": False,
                "path_order": "unresolved",
                "mfe_1h_pct": 0.0,
                "mfe_24h_pct": 0.0,
                "mae_24h_pct": 0.0,
                "abs_mae_24h_pct": 0.0,
                "mfe_before_dd8_pct": 0.0,
                "mae_before_hit_10pct": 0.0,
                "mae_after_hit_10pct": None,
                "time_to_hit_10pct_minutes": None,
                "time_to_drawdown_8pct_minutes": None,
                "path_results": {"target_10_dd_8": {"hit": False}},
            }
        ]
    )

    assert summary["unresolved_24h_count"] == 1


def test_summarize_evaluated_signals_excludes_incomplete_labels_from_denominator():
    rows = [
        {
            "label_complete_1h": True,
            "label_complete_4h": True,
            "label_complete_24h": True,
            "hit_10pct_1h": True,
            "hit_10pct_4h": True,
            "hit_10pct_24h": True,
            "hit_10pct_before_drawdown_8pct": True,
            "hit_10pct_first": True,
            "drawdown_8pct_first": False,
            "ambiguous_same_bar": False,
            "mfe_24h_pct": 20.0,
            "mae_24h_pct": -3.0,
            "abs_mae_24h_pct": 3.0,
            "time_to_hit_10pct_minutes": 5.0,
            "path_results": {"target_10_dd_8": {"hit": True}},
        },
        {
            "label_complete_1h": True,
            "label_complete_4h": True,
            "label_complete_24h": False,
            "hit_10pct_1h": False,
            "hit_10pct_4h": False,
            "hit_10pct_24h": False,
            "hit_10pct_before_drawdown_8pct": False,
            "hit_10pct_first": False,
            "drawdown_8pct_first": False,
            "ambiguous_same_bar": False,
            "mfe_24h_pct": 0.0,
            "mae_24h_pct": 0.0,
            "abs_mae_24h_pct": 0.0,
            "time_to_hit_10pct_minutes": None,
            "path_results": {"target_10_dd_8": {"hit": False}},
        },
    ]

    summary = _MODULE.summarize_evaluated_signals(rows, signal_family="ignition")

    assert summary["signal_count"] == 2
    assert summary["primary_label_complete_count"] == 1
    assert summary["incomplete_label_count"] == 1
    assert summary["hit10_24h_rate"] == 1.0
    assert summary["precision_before_dd8"] == 1.0
    assert summary["avg_mae_24h_pct"] == -3.0
    assert summary["avg_abs_mae_24h_pct"] == 3.0
    assert summary["ambiguous_same_bar_count"] == 0


def test_determine_coverage_status_marks_insufficient_forward_coverage():
    summary = {
        "signal_count": 100,
        "primary_label_complete_count": 94,
        "incomplete_label_count": 6,
    }

    status = _MODULE.determine_coverage_status(
        summary,
        window_end=datetime(2026, 4, 24, 0, 0, tzinfo=timezone.utc),
        run_started_at=datetime(2026, 4, 25, 1, 0, tzinfo=timezone.utc),
        benchmark_status="trusted",
    )

    assert status == "insufficient_forward_coverage"


def test_determine_coverage_status_marks_stale_data_for_recent_window_end():
    summary = {
        "signal_count": 20,
        "primary_label_complete_count": 20,
        "incomplete_label_count": 0,
    }

    status = _MODULE.determine_coverage_status(
        summary,
        window_end=datetime(2026, 4, 25, 0, 0, tzinfo=timezone.utc),
        run_started_at=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
        benchmark_status="trusted",
    )

    assert status == "stale_data"


def test_determine_coverage_status_marks_insufficient_signal_count():
    summary = {
        "signal_count": 9,
        "primary_label_complete_count": 9,
        "incomplete_label_count": 0,
    }

    status = _MODULE.determine_coverage_status(
        summary,
        window_end=datetime(2026, 4, 24, 0, 0, tzinfo=timezone.utc),
        run_started_at=datetime(2026, 4, 25, 1, 0, tzinfo=timezone.utc),
        benchmark_status="trusted",
    )

    assert status == "insufficient_signal_count"


def test_check_benchmark_inputs_marks_missing_btc_or_eth():
    frame = pd.DataFrame(
        [
            {"exchange": "binance", "symbol": "BTCUSDT", "ts": pd.Timestamp("2026-04-24T00:00:00Z")},
            {"exchange": "binance", "symbol": "SOLUSDT", "ts": pd.Timestamp("2026-04-24T00:00:00Z")},
        ]
    )

    assert _MODULE.check_benchmark_inputs(frame, "binance") == "benchmark_missing"


def test_resolve_validation_window_uses_explicit_from_to():
    start, end = _MODULE.resolve_validation_window(
        start_value="2026-03-23T00:00:00Z",
        end_value="2026-04-22T00:00:00Z",
        window_days=None,
        now=datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
    )

    assert start.isoformat() == "2026-03-23T00:00:00+00:00"
    assert end.isoformat() == "2026-04-22T00:00:00+00:00"


def test_resolve_validation_window_uses_window_days_and_end_at():
    start, end = _MODULE.resolve_validation_window(
        start_value=None,
        end_value="2026-04-24T00:00:00Z",
        window_days=30,
        now=datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
    )

    assert start.isoformat() == "2026-03-25T00:00:00+00:00"
    assert end.isoformat() == "2026-04-24T00:00:00+00:00"


def test_resolve_validation_window_defaults_to_30_days():
    start, end = _MODULE.resolve_validation_window(
        start_value=None,
        end_value=None,
        window_days=None,
        now=datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
    )

    assert start.isoformat() == "2026-03-25T10:00:00+00:00"
    assert end.isoformat() == "2026-04-24T10:00:00+00:00"


def test_resolve_validation_window_rejects_inverted_range():
    with pytest.raises(ValueError, match="start must be earlier than end"):
        _MODULE.resolve_validation_window(
            start_value="2026-04-24T00:00:00Z",
            end_value="2026-04-24T00:00:00Z",
            window_days=None,
            now=datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
        )


def _comparison_metadata(
    window_start="2026-03-25T00:00:00+00:00",
    window_end="2026-04-24T00:00:00+00:00",
    **overrides,
):
    metadata = {
        "window_start": window_start,
        "window_end": window_end,
        "exchange_universe": ["binance"],
        "symbol_allowlist": [],
        "symbol_blocklist": [],
        "feature_preparation_version": "abc123",
        "selector": "ignition",
        "market_1m_timestamp_semantics": "minute_open_utc",
        "timestamp_semantics": "hour_bucket_start_utc",
        "entry_policy": "hour_close_proxy",
        "forward_scan_start_policy": "signal_available_at_inclusive",
        "primary_label": "+10_before_-8",
        "horizon_hours": 24,
        "validator_version": "v1.1",
        "coverage_status": "trusted",
        "rule_version": "rule:v1",
        "git_sha": "abc123",
    }
    metadata.update(overrides)
    return metadata


def _comparison_summary(**overrides):
    summary = {
        "signal_count": 30,
        "primary_label_complete_count": 30,
        "precision_before_dd8": 0.6,
        "avg_abs_mae_24h_pct": 8.0,
    }
    summary.update(overrides)
    return summary


def test_compare_validation_runs_rejects_window_mismatch():
    baseline = {"metadata": _comparison_metadata(), "summary": {"signal_count": 20}}
    candidate = {
        "metadata": _comparison_metadata(window_end="2026-04-25T00:00:00+00:00"),
        "summary": {"signal_count": 20},
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "comparison_window_mismatch"


def test_compare_validation_runs_rejects_identical_invalid_primary_windows():
    baseline = {
        "metadata": _comparison_metadata(window_start="not-a-date", window_end="also-not-a-date"),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    candidate = {
        "metadata": _comparison_metadata(window_start="not-a-date", window_end="also-not-a-date"),
        "summary": _comparison_summary(precision_before_dd8=0.8, avg_abs_mae_24h_pct=4.0),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "invalid_comparison_window"
    assert result["invalid_field"] == "baseline_window_start"
    assert result["comparison_window_start"] is None
    assert result["comparison_window_end"] is None


def test_compare_validation_runs_compares_equivalent_utc_primary_windows_as_equal():
    baseline = {
        "metadata": _comparison_metadata(
            window_start="2026-03-25T00:00:00Z",
            window_end="2026-04-24T00:00:00Z",
        ),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    candidate = {
        "metadata": _comparison_metadata(
            window_start="2026-03-25T00:00:00+00:00",
            window_end="2026-04-24T00:00:00+00:00",
        ),
        "summary": _comparison_summary(precision_before_dd8=0.8, avg_abs_mae_24h_pct=4.0),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "evidence_backed"
    assert result["reason"] == "metrics_pass"
    assert result["comparison_window_start"] == "2026-03-25T00:00:00+00:00"
    assert result["comparison_window_end"] == "2026-04-24T00:00:00+00:00"


@pytest.mark.parametrize(
    ("field", "candidate_value"),
    [
        ("market_1m_timestamp_semantics", "minute_close_utc"),
        ("forward_scan_start_policy", "entry_ts_exclusive"),
        ("horizon_hours", 48),
        ("validator_version", "v1.0"),
    ],
)
def test_compare_validation_runs_rejects_timestamp_and_horizon_semantic_mismatch(field, candidate_value):
    baseline = {"metadata": _comparison_metadata(), "summary": _comparison_summary()}
    candidate = {
        "metadata": {**_comparison_metadata(), field: candidate_value},
        "summary": _comparison_summary(precision_before_dd8=0.8, avg_abs_mae_24h_pct=4.0),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "comparison_context_mismatch"
    assert result["mismatched_field"] == field


@pytest.mark.parametrize("field", ["timestamp_semantics", "primary_label"])
def test_compare_validation_runs_rejects_missing_required_primary_context_on_both_sides(field):
    baseline_metadata = _comparison_metadata()
    candidate_metadata = _comparison_metadata()
    baseline_metadata.pop(field)
    candidate_metadata.pop(field)
    baseline = {"metadata": baseline_metadata, "summary": _comparison_summary()}
    candidate = {
        "metadata": candidate_metadata,
        "summary": _comparison_summary(precision_before_dd8=0.8, avg_abs_mae_24h_pct=4.0),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "comparison_context_missing"
    assert result["missing_field"] == field
    assert result["missing_artifacts"] == ["baseline", "candidate"]


def test_compare_validation_runs_marks_sample_limited():
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": {
            "signal_count": 50,
            "primary_label_complete_count": 4,
            "precision_before_dd8": 0.5,
            "avg_abs_mae_24h_pct": 10.0,
        },
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": {
            "signal_count": 50,
            "primary_label_complete_count": 4,
            "precision_before_dd8": 0.75,
            "avg_abs_mae_24h_pct": 7.0,
        },
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "experimental_only"
    assert result["reason"] == "sample_limited"
    assert result["comparison_window_start"] == "2026-03-25T00:00:00+00:00"
    assert result["comparison_window_end"] == "2026-04-24T00:00:00+00:00"
    assert result["baseline_rule_version"] == "rule:v1"
    assert result["candidate_rule_version"] == "rule:v1"
    assert result["baseline_git_sha"] == "abc123"
    assert result["candidate_git_sha"] == "abc123"
    assert result["baseline_precision_before_dd8"] == 0.5
    assert result["candidate_precision_before_dd8"] == 0.75
    assert result["baseline_avg_abs_mae_24h_pct"] == 10.0
    assert result["candidate_avg_abs_mae_24h_pct"] == 7.0
    assert result["baseline_avg_mae_24h_pct"] is None
    assert result["candidate_avg_mae_24h_pct"] is None
    assert result["mae_path_risk_policy"] == "avg_abs_mae_24h_pct"
    assert result["requires_90d"] is False
    assert result["change_classification"] == "non_material"


def test_compare_validation_runs_uses_signed_mae_fallback_when_abs_mae_unavailable():
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_mae_24h_pct=-10.0),
    }
    baseline["summary"].pop("avg_abs_mae_24h_pct")
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_mae_24h_pct=-6.0),
    }
    candidate["summary"].pop("avg_abs_mae_24h_pct")

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "evidence_backed"
    assert result["reason"] == "metrics_pass"
    assert result["mae_path_risk_policy"] == "avg_mae_24h_pct_abs_distance"
    assert result["baseline_avg_abs_mae_24h_pct"] is None
    assert result["candidate_avg_abs_mae_24h_pct"] is None
    assert result["baseline_avg_mae_24h_pct"] == -10.0
    assert result["candidate_avg_mae_24h_pct"] == -6.0
    assert result["path_risk_pass"] is True


def test_compare_validation_runs_rejects_inconsistent_signed_mae_with_abs_mae():
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(
            precision_before_dd8=0.5,
            avg_abs_mae_24h_pct=6.0,
            avg_mae_24h_pct=-10.0,
        ),
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(
            precision_before_dd8=0.6,
            avg_abs_mae_24h_pct=7.0,
            avg_mae_24h_pct=-7.0,
        ),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "invalid_comparison_metric"
    assert result["invalid_field"] == "baseline_avg_mae_24h_pct"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("baseline_precision_before_dd8", 1.1),
        ("baseline_precision_before_dd8", -0.1),
        ("candidate_precision_before_dd8", 1.1),
        ("candidate_precision_before_dd8", -0.1),
    ],
)
def test_compare_validation_runs_rejects_out_of_range_precision(field, value):
    baseline_summary = _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0)
    candidate_summary = _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0)
    if field.startswith("baseline"):
        baseline_summary["precision_before_dd8"] = value
    else:
        candidate_summary["precision_before_dd8"] = value

    result = _MODULE.compare_validation_runs(
        {"metadata": _comparison_metadata(), "summary": baseline_summary},
        {"metadata": _comparison_metadata(), "summary": candidate_summary},
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "invalid_comparison_metric"
    assert result["invalid_field"] == field


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("baseline_avg_abs_mae_24h_pct", -1.0),
        ("candidate_avg_abs_mae_24h_pct", -2.0),
    ],
)
def test_compare_validation_runs_rejects_negative_avg_abs_mae(field, value):
    baseline_summary = _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0)
    candidate_summary = _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=7.0)
    if field.startswith("baseline"):
        baseline_summary["avg_abs_mae_24h_pct"] = value
    else:
        candidate_summary["avg_abs_mae_24h_pct"] = value

    result = _MODULE.compare_validation_runs(
        {"metadata": _comparison_metadata(), "summary": baseline_summary},
        {"metadata": _comparison_metadata(), "summary": candidate_summary},
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "invalid_comparison_metric"
    assert result["invalid_field"] == field


def test_compare_validation_runs_rejects_positive_signed_mae():
    baseline_summary = _comparison_summary(precision_before_dd8=0.6, avg_mae_24h_pct=5.0)
    baseline_summary.pop("avg_abs_mae_24h_pct")
    candidate_summary = _comparison_summary(precision_before_dd8=0.6, avg_mae_24h_pct=-6.0)
    candidate_summary.pop("avg_abs_mae_24h_pct")

    result = _MODULE.compare_validation_runs(
        {"metadata": _comparison_metadata(), "summary": baseline_summary},
        {"metadata": _comparison_metadata(), "summary": candidate_summary},
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "invalid_comparison_metric"
    assert result["invalid_field"] == "baseline_avg_mae_24h_pct"


def test_compare_validation_runs_requires_90d_metrics_to_support_evidence_backed():
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }
    ninety_day_baseline = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": _comparison_summary(
            signal_count=60,
            primary_label_complete_count=60,
            precision_before_dd8=0.5,
            avg_abs_mae_24h_pct=10.0,
        ),
    }
    ninety_day_candidate = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": _comparison_summary(
            signal_count=60,
            primary_label_complete_count=60,
            precision_before_dd8=0.4,
            avg_abs_mae_24h_pct=8.0,
        ),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=True,
        change_classification="material",
        ninety_day_baseline=ninety_day_baseline,
        ninety_day_candidate=ninety_day_candidate,
    )

    assert result["status"] == "not_supported"
    assert result["reason"] == "metrics_do_not_pass_90d"


def test_compare_validation_runs_accepts_evidence_backed_when_90d_supports_change():
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }
    ninety_day_baseline = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": _comparison_summary(
            signal_count=60,
            primary_label_complete_count=60,
            precision_before_dd8=0.5,
            avg_abs_mae_24h_pct=10.0,
        ),
    }
    ninety_day_candidate = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": _comparison_summary(
            signal_count=60,
            primary_label_complete_count=60,
            precision_before_dd8=0.6,
            avg_abs_mae_24h_pct=8.0,
        ),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=True,
        change_classification="material",
        ninety_day_baseline=ninety_day_baseline,
        ninety_day_candidate=ninety_day_candidate,
    )

    assert result["status"] == "evidence_backed"
    assert result["reason"] == "metrics_pass"


def test_compare_validation_runs_requires_precision_metric():
    baseline = {"metadata": _comparison_metadata(), "summary": _comparison_summary()}
    baseline["summary"].pop("precision_before_dd8")
    candidate = {"metadata": _comparison_metadata(), "summary": _comparison_summary()}

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "missing_comparison_metric"
    assert result["invalid_field"] == "baseline_precision_before_dd8"


def test_compare_validation_runs_requires_mae_metric_or_signed_mae_fallback():
    baseline = {"metadata": _comparison_metadata(), "summary": _comparison_summary()}
    baseline["summary"].pop("avg_abs_mae_24h_pct")
    candidate = {"metadata": _comparison_metadata(), "summary": _comparison_summary()}
    candidate["summary"].pop("avg_abs_mae_24h_pct")

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "missing_comparison_metric"
    assert result["invalid_field"] == "baseline_avg_mae_24h_pct"


def test_compare_validation_runs_does_not_use_signal_count_as_complete_count_fallback():
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": {
            "signal_count": 50,
            "precision_before_dd8": 0.5,
            "avg_abs_mae_24h_pct": 10.0,
        },
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": {
            "signal_count": 50,
            "primary_label_complete_count": 0,
            "precision_before_dd8": 0.75,
            "avg_abs_mae_24h_pct": 7.0,
        },
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "experimental_only"
    assert result["reason"] == "sample_limited"
    assert result["baseline_signal_count"] == 50
    assert result["candidate_signal_count"] == 50
    assert result["baseline_primary_label_complete_count"] == 0
    assert result["candidate_primary_label_complete_count"] == 0


def test_compare_validation_runs_rejects_primary_complete_count_above_signal_count():
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(
            signal_count=0,
            primary_label_complete_count=30,
            precision_before_dd8=0.5,
            avg_abs_mae_24h_pct=10.0,
        ),
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "invalid_comparison_metric"
    assert result["invalid_field"] == "baseline_primary_label_complete_count"
    assert result["invalid_count_reason"] == "primary_label_complete_count_exceeds_signal_count"


@pytest.mark.parametrize(
    ("summary_overrides", "expected_field", "expected_count_reason"),
    [
        ({"signal_count": -1}, "baseline_signal_count", "signal_count_negative"),
        (
            {"primary_label_complete_count": -1},
            "baseline_primary_label_complete_count",
            "primary_label_complete_count_negative",
        ),
    ],
)
def test_compare_validation_runs_rejects_negative_primary_counts(
    summary_overrides,
    expected_field,
    expected_count_reason,
):
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": {
            **_comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
            **summary_overrides,
        },
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "invalid_comparison_metric"
    assert result["invalid_field"] == expected_field
    assert result["invalid_count_reason"] == expected_count_reason


@pytest.mark.parametrize(
    "baseline",
    [
        {"summary": {"signal_count": 20}},
        {"metadata": _comparison_metadata()},
        {"metadata": [], "summary": {"signal_count": 20}},
        {"metadata": _comparison_metadata(), "summary": []},
    ],
)
def test_compare_validation_runs_returns_invalid_artifact_for_bad_shape(baseline):
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": {"signal_count": 30, "primary_label_complete_count": 30},
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "invalid_comparison_artifact"


@pytest.mark.parametrize("bad_value", ["bad", float("nan")])
def test_compare_validation_runs_returns_invalid_metric_for_bad_numeric_value(bad_value):
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": {
            "signal_count": 30,
            "primary_label_complete_count": 30,
            "precision_before_dd8": bad_value,
            "avg_abs_mae_24h_pct": 10.0,
        },
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": {
            "signal_count": 30,
            "primary_label_complete_count": 30,
            "precision_before_dd8": 0.6,
            "avg_abs_mae_24h_pct": 8.0,
        },
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "invalid_comparison_metric"
    assert result["invalid_field"] == "baseline_precision_before_dd8"
    assert result["baseline_signal_count"] == 30
    assert result["candidate_signal_count"] == 30
    assert result["baseline_primary_label_complete_count"] == 30
    assert result["candidate_primary_label_complete_count"] == 30
    json.dumps(result, allow_nan=False)


def test_compare_validation_runs_returns_invalid_metric_for_oversized_json_integer():
    oversized_integer = 10**400
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": {
            "signal_count": oversized_integer,
            "primary_label_complete_count": 30,
            "precision_before_dd8": 0.5,
            "avg_abs_mae_24h_pct": 10.0,
        },
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": {
            "signal_count": 30,
            "primary_label_complete_count": 30,
            "precision_before_dd8": 0.6,
            "avg_abs_mae_24h_pct": 8.0,
        },
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "invalid_comparison_metric"
    assert result["invalid_field"] == "baseline_signal_count"
    json.dumps(result, allow_nan=False)


def test_numeric_safety_helpers_do_not_raise_for_oversized_integer():
    oversized_integer = 10**400

    _MODULE._validate_finite_json_numbers({"metadata_only": oversized_integer}, "test artifact")
    int_value, int_error = _MODULE._safe_finite_int({"signal_count": oversized_integer}, "signal_count", "signal_count")
    float_value, float_error = _MODULE._safe_finite_float(
        {"precision_before_dd8": oversized_integer},
        "precision_before_dd8",
        "precision_before_dd8",
    )

    assert int_value is None
    assert int_error == {
        "status": "insufficient",
        "reason": "invalid_comparison_metric",
        "invalid_field": "signal_count",
    }
    assert float_value is None
    assert float_error == {
        "status": "insufficient",
        "reason": "invalid_comparison_metric",
        "invalid_field": "precision_before_dd8",
    }


def test_compare_validation_runs_rejects_untrusted_baseline_coverage():
    baseline = {
        "metadata": {**_comparison_metadata(), "coverage_status": "insufficient_forward_coverage"},
        "summary": _comparison_summary(),
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=False,
        change_classification="non_material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "baseline_insufficient_forward_coverage"


def test_compare_validation_runs_requires_90d_artifacts_when_requested():
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": {
            "signal_count": 30,
            "primary_label_complete_count": 30,
            "precision_before_dd8": 0.5,
            "avg_abs_mae_24h_pct": 10.0,
        },
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": {
            "signal_count": 30,
            "primary_label_complete_count": 30,
            "precision_before_dd8": 0.6,
            "avg_abs_mae_24h_pct": 8.0,
        },
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=True,
        change_classification="material",
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "missing_required_90d_review"


def test_compare_validation_runs_requires_trusted_90d_review_for_material_change():
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": {
            "signal_count": 30,
            "primary_label_complete_count": 30,
            "precision_before_dd8": 0.5,
            "avg_abs_mae_24h_pct": 10.0,
        },
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": {
            "signal_count": 30,
            "primary_label_complete_count": 30,
            "precision_before_dd8": 0.6,
            "avg_abs_mae_24h_pct": 8.0,
        },
    }
    ninety_day_baseline = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": {
            "signal_count": 60,
            "primary_label_complete_count": 60,
            "precision_before_dd8": 0.5,
            "avg_abs_mae_24h_pct": 10.0,
        },
    }
    ninety_day_candidate = {
        "metadata": {**_comparison_metadata(window_start="2026-01-24T00:00:00+00:00"), "coverage_status": "material_gaps"},
        "summary": {
            "signal_count": 60,
            "primary_label_complete_count": 60,
            "precision_before_dd8": 0.6,
            "avg_abs_mae_24h_pct": 8.0,
        },
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=True,
        change_classification="material",
        ninety_day_baseline=ninety_day_baseline,
        ninety_day_candidate=ninety_day_candidate,
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "candidate_90d_material_gaps"


def test_compare_validation_runs_rejects_trusted_30d_baseline_as_required_90d_review():
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }
    ninety_day_candidate = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=True,
        change_classification="material",
        ninety_day_baseline=baseline,
        ninety_day_candidate=ninety_day_candidate,
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "baseline_90d_window_too_short"


def test_compare_validation_runs_rejects_trusted_30d_candidate_as_required_90d_review():
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }
    ninety_day_baseline = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=True,
        change_classification="material",
        ninety_day_baseline=ninety_day_baseline,
        ninety_day_candidate=candidate,
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "candidate_90d_window_too_short"


@pytest.mark.parametrize(
    "metadata_overrides",
    [
        {"window_start": None},
        {"window_end": None},
        {"window_start": "not-a-date"},
    ],
)
def test_compare_validation_runs_handles_malformed_required_90d_window_as_insufficient(metadata_overrides):
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }
    ninety_day_baseline = {
        "metadata": {**_comparison_metadata(window_start="2026-01-24T00:00:00+00:00"), **metadata_overrides},
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    ninety_day_candidate = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=True,
        change_classification="material",
        ninety_day_baseline=ninety_day_baseline,
        ninety_day_candidate=ninety_day_candidate,
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "baseline_90d_window_too_short"


@pytest.mark.parametrize(
    ("candidate_metadata_overrides", "mismatched_field"),
    [
        ({"window_end": "2026-04-25T00:00:00+00:00"}, "window_end"),
        ({"timestamp_semantics": "hour_bucket_end_utc"}, "timestamp_semantics"),
    ],
)
def test_compare_validation_runs_rejects_90d_context_mismatch(candidate_metadata_overrides, mismatched_field):
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }
    ninety_day_baseline = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    ninety_day_candidate = {
        "metadata": {
            **_comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
            **candidate_metadata_overrides,
        },
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=True,
        change_classification="material",
        ninety_day_baseline=ninety_day_baseline,
        ninety_day_candidate=ninety_day_candidate,
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "comparison_90d_context_mismatch"
    assert result["mismatched_90d_field"] == mismatched_field


def test_compare_validation_runs_rejects_90d_context_that_matches_itself_but_not_primary():
    baseline = {
        "metadata": _comparison_metadata(selector="ignition"),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    candidate = {
        "metadata": _comparison_metadata(selector="ignition"),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }
    ninety_day_metadata = _comparison_metadata(
        window_start="2026-01-24T00:00:00+00:00",
        selector="different_selector",
    )
    ninety_day_baseline = {
        "metadata": ninety_day_metadata,
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    ninety_day_candidate = {
        "metadata": dict(ninety_day_metadata),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=True,
        change_classification="material",
        ninety_day_baseline=ninety_day_baseline,
        ninety_day_candidate=ninety_day_candidate,
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "comparison_90d_context_mismatch"
    assert result["mismatched_90d_field"] == "selector"
    assert result["mismatched_90d_artifact"] == "baseline_90d"


@pytest.mark.parametrize("field", ["timestamp_semantics", "primary_label"])
def test_compare_validation_runs_rejects_required_90d_with_missing_context_on_both_sides(field):
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }
    baseline_90d_metadata = _comparison_metadata(window_start="2026-01-24T00:00:00+00:00")
    candidate_90d_metadata = _comparison_metadata(window_start="2026-01-24T00:00:00+00:00")
    baseline_90d_metadata.pop(field)
    candidate_90d_metadata.pop(field)
    ninety_day_baseline = {
        "metadata": baseline_90d_metadata,
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    ninety_day_candidate = {
        "metadata": candidate_90d_metadata,
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=True,
        change_classification="material",
        ninety_day_baseline=ninety_day_baseline,
        ninety_day_candidate=ninety_day_candidate,
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "comparison_90d_context_missing"
    assert result["missing_90d_field"] == field
    assert result["missing_90d_artifacts"] == ["baseline", "candidate"]


def test_compare_validation_runs_rejects_90d_window_end_mismatched_from_primary_window_end():
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }
    ninety_day_baseline = {
        "metadata": _comparison_metadata(
            window_start="2026-01-25T00:00:00+00:00",
            window_end="2026-04-25T00:00:00+00:00",
        ),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    ninety_day_candidate = {
        "metadata": _comparison_metadata(
            window_start="2026-01-25T00:00:00+00:00",
            window_end="2026-04-25T00:00:00+00:00",
        ),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=True,
        change_classification="material",
        ninety_day_baseline=ninety_day_baseline,
        ninety_day_candidate=ninety_day_candidate,
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "comparison_90d_context_mismatch"
    assert result["mismatched_90d_field"] == "window_end"


@pytest.mark.parametrize(
    ("summary_overrides", "expected_reason", "expected_field"),
    [
        (
            {"precision_before_dd8": "bad"},
            "invalid_comparison_metric",
            "baseline_90d_precision_before_dd8",
        ),
        (
            {"signal_count": 10**400},
            "invalid_comparison_metric",
            "baseline_90d_signal_count",
        ),
        (
            {"primary_label_complete_count": float("nan")},
            "invalid_comparison_metric",
            "baseline_90d_primary_label_complete_count",
        ),
        (
            {"primary_label_complete_count": 31},
            "invalid_comparison_metric",
            "baseline_90d_primary_label_complete_count",
        ),
        (
            {"signal_count": -1},
            "invalid_comparison_metric",
            "baseline_90d_signal_count",
        ),
        (
            {"primary_label_complete_count": -1},
            "invalid_comparison_metric",
            "baseline_90d_primary_label_complete_count",
        ),
    ],
)
def test_compare_validation_runs_rejects_malformed_required_90d_summary_metrics(
    summary_overrides,
    expected_reason,
    expected_field,
):
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }
    ninety_day_baseline = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": {
            **_comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
            **summary_overrides,
        },
    }
    ninety_day_candidate = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=True,
        change_classification="material",
        ninety_day_baseline=ninety_day_baseline,
        ninety_day_candidate=ninety_day_candidate,
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == expected_reason
    assert result["invalid_field"] == expected_field


def test_compare_validation_runs_rejects_impossible_candidate_90d_counts_for_material_review():
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }
    ninety_day_baseline = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    ninety_day_candidate = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": _comparison_summary(
            signal_count=0,
            primary_label_complete_count=30,
            precision_before_dd8=0.6,
            avg_abs_mae_24h_pct=8.0,
        ),
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=True,
        change_classification="material",
        ninety_day_baseline=ninety_day_baseline,
        ninety_day_candidate=ninety_day_candidate,
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "invalid_comparison_metric"
    assert result["invalid_field"] == "candidate_90d_primary_label_complete_count"
    assert result["invalid_count_reason"] == "primary_label_complete_count_exceeds_signal_count"


def test_compare_validation_runs_rejects_missing_candidate_90d_summary_metrics():
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }
    ninety_day_baseline = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    ninety_day_candidate = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": {},
    }

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=True,
        change_classification="material",
        ninety_day_baseline=ninety_day_baseline,
        ninety_day_candidate=ninety_day_candidate,
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "missing_comparison_metric"
    assert result["invalid_field"] == "candidate_90d_signal_count"


def test_compare_validation_runs_rejects_malformed_one_sided_90d_mae_metric():
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.5, avg_abs_mae_24h_pct=10.0),
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_abs_mae_24h_pct=8.0),
    }
    ninety_day_baseline = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": _comparison_summary(
            precision_before_dd8=0.5,
            avg_abs_mae_24h_pct="bad",
            avg_mae_24h_pct=-10.0,
        ),
    }
    ninety_day_candidate = {
        "metadata": _comparison_metadata(window_start="2026-01-24T00:00:00+00:00"),
        "summary": _comparison_summary(precision_before_dd8=0.6, avg_mae_24h_pct=-6.0),
    }
    ninety_day_candidate["summary"].pop("avg_abs_mae_24h_pct")

    result = _MODULE.compare_validation_runs(
        baseline,
        candidate,
        require_90d=True,
        change_classification="material",
        ninety_day_baseline=ninety_day_baseline,
        ninety_day_candidate=ninety_day_candidate,
    )

    assert result["status"] == "insufficient"
    assert result["reason"] == "invalid_comparison_metric"
    assert result["invalid_field"] == "baseline_90d_avg_abs_mae_24h_pct"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "compare_baseline_config",
            "baseline.json",
            "--compare-baseline-config and --compare-candidate-config must be provided together",
        ),
        (
            "compare_candidate_config",
            "candidate.json",
            "--compare-baseline-config and --compare-candidate-config must be provided together",
        ),
        (
            "compare_90d_baseline_config",
            "baseline-90d.json",
            "--compare-90d-baseline-config and --compare-90d-candidate-config must be provided together",
        ),
        (
            "compare_90d_candidate_config",
            "candidate-90d.json",
            "--compare-90d-baseline-config and --compare-90d-candidate-config must be provided together",
        ),
        ("require_90d", True, "--require-90d is only valid in comparison mode"),
        ("change_classification", "material", "--change-classification material is only valid in comparison mode"),
    ],
)
def test_validate_cli_mode_rejects_invalid_compare_flag_combinations(capsys, field, value, message):
    parser = argparse.ArgumentParser(prog="validate_ultra_signal_production.py")
    args = argparse.Namespace(
        compare_baseline_config=None,
        compare_candidate_config=None,
        compare_90d_baseline_config=None,
        compare_90d_candidate_config=None,
        require_90d=False,
        change_classification="non_material",
    )
    setattr(args, field, value)

    with pytest.raises(SystemExit) as exc_info:
        _MODULE._validate_cli_mode(parser, args)

    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err


def test_validate_cli_mode_allows_paired_comparison_configs():
    parser = argparse.ArgumentParser(prog="validate_ultra_signal_production.py")
    args = argparse.Namespace(
        compare_baseline_config="baseline.json",
        compare_candidate_config="candidate.json",
        compare_90d_baseline_config=None,
        compare_90d_candidate_config=None,
        require_90d=False,
        change_classification="non_material",
    )

    assert _MODULE._validate_cli_mode(parser, args) is None


def test_validate_cli_mode_allows_default_normal_validation_args():
    parser = argparse.ArgumentParser(prog="validate_ultra_signal_production.py")
    args = argparse.Namespace(
        compare_baseline_config=None,
        compare_candidate_config=None,
        compare_90d_baseline_config=None,
        compare_90d_candidate_config=None,
        require_90d=False,
        change_classification="non_material",
    )

    assert _MODULE._validate_cli_mode(parser, args) is None


def test_load_comparison_config_resolves_paths_relative_to_config(tmp_path, monkeypatch):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "summary.json").write_text(
        json.dumps({"signal_count": 30, "primary_label_complete_count": 30}),
        encoding="utf-8",
    )
    (config_dir / "metadata.json").write_text(json.dumps(_comparison_metadata()), encoding="utf-8")
    config_path = config_dir / "comparison.json"
    config_path.write_text(
        json.dumps({"summary_path": "summary.json", "metadata_path": "metadata.json"}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = _MODULE.load_comparison_config(str(config_path))

    assert result["summary"]["signal_count"] == 30
    assert result["metadata"]["selector"] == "ignition"


@pytest.mark.parametrize(
    ("artifact_name", "artifact_text", "message"),
    [
        ("summary.json", '{"signal_count": NaN}', "summary_path"),
        ("metadata.json", '{"window_start": NaN}', "metadata_path"),
    ],
)
def test_load_comparison_config_rejects_non_standard_json_constants(tmp_path, artifact_name, artifact_text, message):
    summary_path = tmp_path / "summary.json"
    metadata_path = tmp_path / "metadata.json"
    summary_path.write_text(json.dumps({"signal_count": 30, "primary_label_complete_count": 30}), encoding="utf-8")
    metadata_path.write_text(json.dumps(_comparison_metadata()), encoding="utf-8")
    (tmp_path / artifact_name).write_text(artifact_text, encoding="utf-8")
    config_path = tmp_path / "comparison.json"
    config_path.write_text(
        json.dumps({"summary_path": "summary.json", "metadata_path": "metadata.json"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=rf"{message} for comparison config .*{artifact_name}.*NaN"):
        _MODULE.load_comparison_config(str(config_path))


@pytest.mark.parametrize(
    ("artifact_name", "artifact_text", "message"),
    [
        ("summary.json", '{"signal_count": 1e999}', "summary_path"),
        ("metadata.json", '{"window_start": 1e999}', "metadata_path"),
    ],
)
def test_load_comparison_config_rejects_overflowing_json_numbers(tmp_path, artifact_name, artifact_text, message):
    summary_path = tmp_path / "summary.json"
    metadata_path = tmp_path / "metadata.json"
    summary_path.write_text(json.dumps({"signal_count": 30, "primary_label_complete_count": 30}), encoding="utf-8")
    metadata_path.write_text(json.dumps(_comparison_metadata()), encoding="utf-8")
    (tmp_path / artifact_name).write_text(artifact_text, encoding="utf-8")
    config_path = tmp_path / "comparison.json"
    config_path.write_text(
        json.dumps({"summary_path": "summary.json", "metadata_path": "metadata.json"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=rf"{message} for comparison config .*{artifact_name}.*non-finite number"):
        _MODULE.load_comparison_config(str(config_path))


@pytest.mark.parametrize(
    "config",
    [
        {"metadata_path": "metadata.json"},
        {"summary_path": "summary.json"},
    ],
)
def test_load_comparison_config_rejects_missing_required_paths(tmp_path, config):
    config_path = tmp_path / "comparison.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match="comparison config .* requires summary_path and metadata_path"):
        _MODULE.load_comparison_config(str(config_path))


def test_main_comparison_mode_writes_artifacts_for_bad_config(tmp_path, monkeypatch, capsys):
    bad_config_path = tmp_path / "bad-config.json"
    candidate_config_path = tmp_path / "candidate-config.json"
    output_root = tmp_path / "comparison-output"
    bad_config_path.write_text(json.dumps({"metadata_path": "metadata.json"}), encoding="utf-8")
    candidate_config_path.write_text(json.dumps({"metadata_path": "metadata.json"}), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_ultra_signal_production.py",
            "--compare-baseline-config",
            str(bad_config_path),
            "--compare-candidate-config",
            str(candidate_config_path),
            "--output-root",
            str(output_root),
        ],
    )

    assert _MODULE.main() == 0

    comparison_paths = list(output_root.glob("*-comparison.json"))
    readme_paths = list(output_root.glob("*-comparison_README.md"))
    assert len(comparison_paths) == 1
    assert len(readme_paths) == 1
    result = json.loads(comparison_paths[0].read_text(encoding="utf-8"))
    assert result["status"] == "insufficient"
    assert result["reason"] == "invalid_comparison_config"
    assert "requires summary_path and metadata_path" in result["error"]
    assert "NaN" not in comparison_paths[0].read_text(encoding="utf-8")
    assert "Infinity" not in comparison_paths[0].read_text(encoding="utf-8")
    assert "comparison_path=" in capsys.readouterr().out


def test_main_comparison_mode_writes_artifacts_for_overflowing_config_number(tmp_path, monkeypatch, capsys):
    summary_path = tmp_path / "summary.json"
    metadata_path = tmp_path / "metadata.json"
    bad_config_path = tmp_path / "bad-config.json"
    candidate_config_path = tmp_path / "candidate-config.json"
    output_root = tmp_path / "comparison-output"
    summary_path.write_text(json.dumps({"signal_count": 30, "primary_label_complete_count": 30}), encoding="utf-8")
    metadata_path.write_text('{"window_start": 1e999}', encoding="utf-8")
    bad_config_path.write_text(
        json.dumps({"summary_path": "summary.json", "metadata_path": "metadata.json"}),
        encoding="utf-8",
    )
    candidate_config_path.write_text(
        json.dumps({"summary_path": "summary.json", "metadata_path": "metadata.json"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_ultra_signal_production.py",
            "--compare-baseline-config",
            str(bad_config_path),
            "--compare-candidate-config",
            str(candidate_config_path),
            "--output-root",
            str(output_root),
        ],
    )

    assert _MODULE.main() == 0

    comparison_paths = list(output_root.glob("*-comparison.json"))
    assert len(comparison_paths) == 1
    artifact_text = comparison_paths[0].read_text(encoding="utf-8")
    result = json.loads(artifact_text)
    assert result["status"] == "insufficient"
    assert result["reason"] == "invalid_comparison_config"
    assert "non-finite number" in result["error"]
    assert "NaN" not in artifact_text
    assert "Infinity" not in artifact_text
    assert "comparison_path=" in capsys.readouterr().out


def test_main_comparison_mode_writes_artifacts_for_oversized_integer_metric(tmp_path, monkeypatch, capsys):
    summary_path = tmp_path / "summary.json"
    candidate_summary_path = tmp_path / "candidate-summary.json"
    metadata_path = tmp_path / "metadata.json"
    bad_config_path = tmp_path / "bad-config.json"
    candidate_config_path = tmp_path / "candidate-config.json"
    output_root = tmp_path / "comparison-output"
    summary_path.write_text(
        json.dumps(
            {
                "signal_count": 10**400,
                "primary_label_complete_count": 30,
                "precision_before_dd8": 0.5,
                "avg_abs_mae_24h_pct": 10.0,
            }
        ),
        encoding="utf-8",
    )
    candidate_summary_path.write_text(
        json.dumps(
            {
                "signal_count": 30,
                "primary_label_complete_count": 30,
                "precision_before_dd8": 0.6,
                "avg_abs_mae_24h_pct": 8.0,
            }
        ),
        encoding="utf-8",
    )
    metadata_path.write_text(json.dumps(_comparison_metadata()), encoding="utf-8")
    bad_config_path.write_text(
        json.dumps({"summary_path": "summary.json", "metadata_path": "metadata.json"}),
        encoding="utf-8",
    )
    candidate_config_path.write_text(
        json.dumps({"summary_path": "candidate-summary.json", "metadata_path": "metadata.json"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_ultra_signal_production.py",
            "--compare-baseline-config",
            str(bad_config_path),
            "--compare-candidate-config",
            str(candidate_config_path),
            "--output-root",
            str(output_root),
        ],
    )

    assert _MODULE.main() == 0

    comparison_paths = list(output_root.glob("*-comparison.json"))
    assert len(comparison_paths) == 1
    artifact_text = comparison_paths[0].read_text(encoding="utf-8")
    result = json.loads(artifact_text)
    assert result["status"] == "insufficient"
    assert result["reason"] == "invalid_comparison_metric"
    assert result["invalid_field"] == "baseline_signal_count"
    assert "NaN" not in artifact_text
    assert "Infinity" not in artifact_text
    assert "comparison_path=" in capsys.readouterr().out


def test_main_comparison_mode_writes_artifacts_for_non_finite_comparison_result(tmp_path, monkeypatch):
    output_root = tmp_path / "comparison-output"

    monkeypatch.setattr(_MODULE, "load_comparison_config", lambda path: {"metadata": {}, "summary": {}})
    monkeypatch.setattr(
        _MODULE,
        "compare_validation_runs",
        lambda *args, **kwargs: {"status": "insufficient", "comparison_window_start": float("nan")},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_ultra_signal_production.py",
            "--compare-baseline-config",
            str(tmp_path / "baseline.json"),
            "--compare-candidate-config",
            str(tmp_path / "candidate.json"),
            "--output-root",
            str(output_root),
        ],
    )

    assert _MODULE.main() == 0

    comparison_paths = list(output_root.glob("*-comparison.json"))
    assert len(comparison_paths) == 1
    artifact_text = comparison_paths[0].read_text(encoding="utf-8")
    result = json.loads(artifact_text)
    assert result["status"] == "insufficient"
    assert result["reason"] == "invalid_comparison_artifact"
    assert "non-finite number" in result["error"]
    assert "NaN" not in artifact_text
    assert "Infinity" not in artifact_text


def test_rule_config_hash_changes_when_rule_config_changes():
    first = _MODULE.rule_config_hash({"min_return_1h_pct": 12.0, "max_return_1h_pct": 35.0})
    second = _MODULE.rule_config_hash({"min_return_1h_pct": 13.0, "max_return_1h_pct": 35.0})

    assert first.startswith("sha256:")
    assert second.startswith("sha256:")
    assert first != second


def test_validate_prepared_features_end_to_end_for_all_families():
    features = pd.DataFrame(
        [
            {
                "asset_id": 1,
                "exchange": "binance",
                "symbol": "BTCUSDT",
                "ts": pd.Timestamp("2026-04-22T10:00:00Z"),
                "close": 100.0,
                "return_1h_pct": 0.0,
                "return_4h_pct": 0.0,
                "return_24h_pct": 0.0,
                "return_7d_pct": 0.0,
                "return_30d_pct": 0.0,
                "volume_ratio_24h": 1.0,
                "quality_score": 100.0,
                "chase_risk_score": 0.0,
                "risk_flags": (),
                "continuation_grade": None,
                "ignition_grade": None,
                "reacceleration_grade": None,
                "ultra_high_conviction": False,
            },
            {
                "asset_id": 2,
                "exchange": "binance",
                "symbol": "ETHUSDT",
                "ts": pd.Timestamp("2026-04-22T10:00:00Z"),
                "close": 100.0,
                "return_1h_pct": 0.0,
                "return_4h_pct": 0.0,
                "return_24h_pct": 0.0,
                "return_7d_pct": 0.0,
                "return_30d_pct": 0.0,
                "volume_ratio_24h": 1.0,
                "quality_score": 100.0,
                "chase_risk_score": 0.0,
                "risk_flags": (),
                "continuation_grade": None,
                "ignition_grade": None,
                "reacceleration_grade": None,
                "ultra_high_conviction": False,
            },
            {
                "asset_id": 3,
                "exchange": "binance",
                "symbol": "CONTUSDT",
                "ts": pd.Timestamp("2026-04-22T10:00:00Z"),
                "close": 100.0,
                "return_1h_pct": 8.0,
                "return_4h_pct": 20.0,
                "return_24h_pct": 35.0,
                "return_7d_pct": 60.0,
                "return_30d_pct": 70.0,
                "volume_ratio_24h": 1.0,
                "quality_score": 90.0,
                "chase_risk_score": 10.0,
                "risk_flags": (),
                "continuation_grade": "A",
                "ignition_grade": None,
                "reacceleration_grade": None,
                "ultra_high_conviction": False,
            },
            {
                "asset_id": 4,
                "exchange": "binance",
                "symbol": "IGNUSDT",
                "ts": pd.Timestamp("2026-04-22T10:00:00Z"),
                "close": 100.0,
                "return_1h_pct": 5.0,
                "return_4h_pct": 12.0,
                "return_24h_pct": 24.0,
                "return_7d_pct": 55.0,
                "return_30d_pct": 40.0,
                "volume_ratio_24h": 1.0,
                "quality_score": 90.0,
                "chase_risk_score": 10.0,
                "risk_flags": (),
                "continuation_grade": None,
                "ignition_grade": "A",
                "reacceleration_grade": None,
                "ultra_high_conviction": False,
            },
            {
                "asset_id": 5,
                "exchange": "binance",
                "symbol": "REACCUSDT",
                "ts": pd.Timestamp("2026-04-22T10:00:00Z"),
                "close": 100.0,
                "return_1h_pct": 4.0,
                "return_4h_pct": 12.0,
                "return_24h_pct": 24.0,
                "return_7d_pct": 55.0,
                "return_30d_pct": 40.0,
                "volume_ratio_24h": 1.0,
                "quality_score": 90.0,
                "chase_risk_score": 10.0,
                "risk_flags": (),
                "continuation_grade": None,
                "ignition_grade": None,
                "reacceleration_grade": "B",
                "ultra_high_conviction": False,
            },
            {
                "asset_id": 6,
                "exchange": "binance",
                "symbol": "ULTRAUSDT",
                "ts": pd.Timestamp("2026-04-22T10:00:00Z"),
                "close": 100.0,
                "return_1h_pct": 15.0,
                "return_4h_pct": 50.0,
                "return_24h_pct": 95.0,
                "return_7d_pct": 120.0,
                "return_30d_pct": 100.0,
                "volume_ratio_24h": 1.0,
                "quality_score": 90.0,
                "chase_risk_score": 30.0,
                "risk_flags": ("ULTRA_HIGH_CONVICTION",),
                "continuation_grade": None,
                "ignition_grade": None,
                "reacceleration_grade": None,
                "ultra_high_conviction": True,
            },
        ]
    )
    future_rows = pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2026-04-22T11:00:00Z") + pd.Timedelta(minutes=minute),
                "open": 100.0,
                "high": 112.0,
                "low": 99.0,
            }
            for minute in range(60)
        ]
    )
    future_rows_by_asset_id = {int(asset_id): future_rows for asset_id in features["asset_id"]}

    outputs = {
        family: _MODULE.evaluate_prepared_feature_frame(
            features,
            future_rows_by_asset_id=future_rows_by_asset_id,
            exchange="binance",
            start=pd.Timestamp("2026-04-22T10:00:00Z").to_pydatetime(),
            end=pd.Timestamp("2026-04-22T11:00:00Z").to_pydatetime(),
            signal_family=family,
        )[0]
        for family in ("continuation", "ignition", "reacceleration", "ultra_high_conviction")
    }

    assert outputs["continuation"]["signal_count"] == 1
    assert outputs["ignition"]["signal_count"] == 1
    assert outputs["reacceleration"]["signal_count"] == 1
    assert outputs["ultra_high_conviction"]["signal_count"] == 1


def test_evaluate_prepared_feature_frame_preserves_signal_identity_and_branch_inputs():
    features = pd.DataFrame(
        [
            {
                "asset_id": 5,
                "exchange": "binance",
                "symbol": "REACCUSDT",
                "ts": pd.Timestamp("2026-04-22T10:00:00Z"),
                "close": 100.0,
                "return_1h_pct": 4.0,
                "return_4h_pct": 12.0,
                "return_24h_pct": 24.0,
                "return_7d_pct": 55.0,
                "return_30d_pct": 40.0,
                "volume_ratio_24h": 3.2,
                "volume_breakout_score": 92.0,
                "return_24h_percentile": 0.82,
                "return_7d_percentile": 0.91,
                "return_30d_percentile": 0.86,
                "quality_score": 90.0,
                "chase_risk_score": 10.0,
                "risk_flags": (),
                "breakout_20d": True,
                "continuation_grade": None,
                "ignition_grade": None,
                "reacceleration_grade": "B",
                "ultra_high_conviction": False,
            }
        ]
    )
    future_rows_by_asset_id = {
        5: pd.DataFrame(
            [
                {
                    "ts": pd.Timestamp("2026-04-22T11:00:00Z") + pd.Timedelta(minutes=minute),
                    "open": 100.0,
                    "high": 112.0,
                    "low": 99.0,
                }
                for minute in range(60)
            ]
        )
    }

    _, rows = _MODULE.evaluate_prepared_feature_frame(
        features,
        future_rows_by_asset_id=future_rows_by_asset_id,
        exchange="binance",
        start=pd.Timestamp("2026-04-22T10:00:00Z").to_pydatetime(),
        end=pd.Timestamp("2026-04-22T11:00:00Z").to_pydatetime(),
        signal_family="reacceleration",
    )

    assert rows[0]["signal_family"] == "reacceleration"
    assert rows[0]["signal_grade"] == "B"
    assert rows[0]["signal_selector"] == "reacceleration"
    assert rows[0]["breakout_20d"] is True
