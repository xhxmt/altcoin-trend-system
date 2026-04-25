import importlib.util
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

    def fake_fetch_forward_1m_rows(engine, asset_id, start_ts, horizon):
        captured["fetch_asset_id"] = asset_id
        captured["fetch_start_ts"] = pd.Timestamp(start_ts)
        captured["fetch_horizon"] = horizon
        return pd.DataFrame([{"ts": available_ts, "high": 111.0, "low": 99.0}])

    def fake_compute_forward_path_labels(scan_start_ts, close, future_1m):
        captured["label_scan_start_ts"] = pd.Timestamp(scan_start_ts)
        return {
            "mfe_1h_pct": 11.0,
            "mfe_4h_pct": 11.0,
            "mfe_24h_pct": 11.0,
            "mae_1h_pct": 0.0,
            "mae_4h_pct": 0.0,
            "mae_24h_pct": 0.0,
            "mfe_before_dd8_pct": 11.0,
            "mae_before_hit_10pct": 0.0,
            "mae_after_hit_10pct": 0.0,
            "hit_10pct_before_drawdown_8pct": True,
            "hit_10pct_first": True,
            "drawdown_8pct_first": False,
            "time_to_hit_10pct_minutes": 0.0,
            "time_to_drawdown_8pct_minutes": None,
        }

    monkeypatch.setattr(_MODULE, "fetch_forward_1m_rows", fake_fetch_forward_1m_rows)
    monkeypatch.setattr(_MODULE, "compute_forward_path_labels", fake_compute_forward_path_labels)

    _, rows = _MODULE.evaluate_signal_family(
        object(),
        "binance",
        datetime(2026, 4, 22, 10, tzinfo=timezone.utc),
        datetime(2026, 4, 22, 11, tzinfo=timezone.utc),
        signal_family="ultra_high_conviction",
    )

    assert rows[0]["ts"] == "2026-04-22T10:00:00+00:00"
    assert captured["fetch_start_ts"].isoformat() == "2026-04-22T11:00:00+00:00"
    assert captured["label_scan_start_ts"].isoformat() == "2026-04-22T11:00:00+00:00"
