import importlib.util
import sys
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
