import json
import importlib.util
from datetime import datetime, timezone
from pathlib import Path


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_ultra_signal_production.py"
_SPEC = importlib.util.spec_from_file_location("validate_ultra_signal_production", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

METADATA_FILENAME = _MODULE.METADATA_FILENAME
README_FILENAME = _MODULE.README_FILENAME
SIGNALS_FILENAME = _MODULE.SIGNALS_FILENAME
SUMMARY_FILENAME = _MODULE.SUMMARY_FILENAME
build_run_metadata = _MODULE.build_run_metadata
summarize_ultra_gate_flow = _MODULE.summarize_ultra_gate_flow
write_artifacts = _MODULE.write_artifacts


def test_build_run_metadata_captures_validation_contract(tmp_path):
    output_dir = tmp_path / "20260423-120603-production-ultra-binance"
    metadata = build_run_metadata(
        exchange="binance",
        start=datetime(2026, 1, 22, 10, 0, tzinfo=timezone.utc),
        end=datetime(2026, 4, 22, 10, 0, tzinfo=timezone.utc),
        market_start=datetime(2025, 12, 22, 10, 0, tzinfo=timezone.utc),
        market_end=datetime(2026, 4, 23, 11, 0, tzinfo=timezone.utc),
        output_dir=output_dir,
        output_root=tmp_path,
        generated_at=datetime(2026, 4, 23, 12, 6, 3, tzinfo=timezone.utc),
    )

    assert metadata["validation_window"] == {
        "from": "2026-01-22T10:00:00+00:00",
        "to": "2026-04-22T10:00:00+00:00",
    }
    assert metadata["warmup_window"]["from"] == "2025-12-22T10:00:00+00:00"
    assert metadata["forward_window"]["to"] == "2026-04-23T11:00:00+00:00"
    assert metadata["expected_outputs"]["summary"] == SUMMARY_FILENAME
    assert metadata["artifacts"]["output_dir"] == str(output_dir)


def test_write_artifacts_writes_summary_signals_metadata_and_readme(tmp_path):
    output_dir = tmp_path / "run"
    summary = {
        "exchange": "binance",
        "from": "2026-01-22T10:00:00+00:00",
        "to": "2026-04-22T10:00:00+00:00",
        "market_from": "2025-12-22T10:00:00+00:00",
        "market_to": "2026-04-23T11:00:00+00:00",
        "gate_flow": {
            "window_feature_rows": 120,
            "pass_top_24h_rank_gate": 8,
            "pass_7d_strength_gate": 6,
            "pass_30d_strength_gate": 5,
            "pass_1h_range": 15,
            "pass_quality_gate": 4,
        },
        "ultra_signal_count": 4,
        "precision_1h": 1.0,
        "precision_4h": 1.0,
        "precision_24h": 1.0,
        "precision_before_dd8": 0.5,
    }
    rows = [{"symbol": "HIGHUSDT", "mfe_1h_pct": 12.0}]
    metadata = build_run_metadata(
        exchange="binance",
        start=datetime(2026, 1, 22, 10, 0, tzinfo=timezone.utc),
        end=datetime(2026, 4, 22, 10, 0, tzinfo=timezone.utc),
        market_start=datetime(2025, 12, 22, 10, 0, tzinfo=timezone.utc),
        market_end=datetime(2026, 4, 23, 11, 0, tzinfo=timezone.utc),
        output_dir=output_dir,
        output_root=tmp_path,
        generated_at=datetime(2026, 4, 23, 12, 6, 3, tzinfo=timezone.utc),
    )

    write_artifacts(output_dir, summary, rows, metadata)

    assert (output_dir / SUMMARY_FILENAME).exists()
    assert (output_dir / SIGNALS_FILENAME).exists()
    assert (output_dir / METADATA_FILENAME).exists()
    assert (output_dir / README_FILENAME).exists()
    assert json.loads((output_dir / SUMMARY_FILENAME).read_text(encoding="utf-8"))["ultra_signal_count"] == 4
    assert json.loads((output_dir / METADATA_FILENAME).read_text(encoding="utf-8"))["expected_outputs"]["signals"] == SIGNALS_FILENAME
    readme = (output_dir / README_FILENAME).read_text(encoding="utf-8")
    assert "validation_window: 2026-01-22T10:00:00+00:00 -> 2026-04-22T10:00:00+00:00" in readme
    assert "signals.csv: per-signal evaluation rows" in readme
    assert "pass_top_24h_rank_gate: 8" in readme
    assert "pass_quality_gate: 4" in readme


def test_write_artifacts_creates_empty_signals_file_when_no_rows(tmp_path):
    output_dir = tmp_path / "run"
    summary = {
        "exchange": "bybit",
        "from": "2026-01-22T10:00:00+00:00",
        "to": "2026-04-22T10:00:00+00:00",
        "market_from": "2025-12-22T10:00:00+00:00",
        "market_to": "2026-04-23T11:00:00+00:00",
        "gate_flow": {"window_feature_rows": 0},
        "ultra_signal_count": 0,
        "precision_1h": 0.0,
        "precision_4h": 0.0,
        "precision_24h": 0.0,
        "precision_before_dd8": 0.0,
    }
    metadata = build_run_metadata(
        exchange="bybit",
        start=datetime(2026, 1, 22, 10, 0, tzinfo=timezone.utc),
        end=datetime(2026, 4, 22, 10, 0, tzinfo=timezone.utc),
        market_start=datetime(2025, 12, 22, 10, 0, tzinfo=timezone.utc),
        market_end=datetime(2026, 4, 23, 11, 0, tzinfo=timezone.utc),
        output_dir=output_dir,
        output_root=tmp_path,
        generated_at=datetime(2026, 4, 23, 12, 6, 3, tzinfo=timezone.utc),
    )

    write_artifacts(output_dir, summary, [], metadata)

    assert (output_dir / SIGNALS_FILENAME).read_text(encoding="utf-8") == ""


def test_summarize_ultra_gate_flow_counts_cumulative_stage_passes():
    rows = [
        {
            "return_1h_pct": 15.0,
            "return_4h_pct": 50.0,
            "return_24h_pct": 60.0,
            "return_30d_pct": 70.0,
            "volume_ratio_24h": 6.0,
            "return_24h_rank": 2,
            "return_24h_percentile": 0.99,
            "return_7d_percentile": 0.99,
            "return_30d_percentile": 0.85,
            "quality_score": 90.0,
            "breakout_20d": True,
            "veto_reason_codes": [],
        },
        {
            "return_1h_pct": 15.0,
            "return_4h_pct": 50.0,
            "return_24h_pct": 60.0,
            "return_30d_pct": 70.0,
            "volume_ratio_24h": 6.0,
            "return_24h_rank": 6,
            "return_24h_percentile": 0.999,
            "return_7d_percentile": 0.99,
            "return_30d_percentile": 0.85,
            "quality_score": 90.0,
            "breakout_20d": True,
            "veto_reason_codes": [],
        },
        {
            "return_1h_pct": 15.0,
            "return_4h_pct": 50.0,
            "return_24h_pct": 60.0,
            "return_30d_pct": 70.0,
            "volume_ratio_24h": 6.0,
            "return_24h_rank": 3,
            "return_24h_percentile": 0.99,
            "return_7d_percentile": 0.98,
            "return_30d_percentile": 0.85,
            "quality_score": 90.0,
            "breakout_20d": True,
            "veto_reason_codes": [],
        },
        {
            "return_1h_pct": 15.0,
            "return_4h_pct": 50.0,
            "return_24h_pct": 60.0,
            "return_30d_pct": 70.0,
            "volume_ratio_24h": 6.0,
            "return_24h_rank": 1,
            "return_24h_percentile": 0.99,
            "return_7d_percentile": 0.99,
            "return_30d_percentile": 0.79,
            "quality_score": 90.0,
            "breakout_20d": True,
            "veto_reason_codes": [],
        },
        {
            "return_1h_pct": 41.0,
            "return_4h_pct": 50.0,
            "return_24h_pct": 60.0,
            "return_30d_pct": 70.0,
            "volume_ratio_24h": 6.0,
            "return_24h_rank": 2,
            "return_24h_percentile": 0.99,
            "return_7d_percentile": 0.99,
            "return_30d_percentile": 0.85,
            "quality_score": 90.0,
            "breakout_20d": True,
            "veto_reason_codes": [],
        },
        {
            "return_1h_pct": 15.0,
            "return_4h_pct": 50.0,
            "return_24h_pct": 60.0,
            "return_30d_pct": 70.0,
            "volume_ratio_24h": 6.0,
            "return_24h_rank": 2,
            "return_24h_percentile": 0.99,
            "return_7d_percentile": 0.99,
            "return_30d_percentile": 0.85,
            "quality_score": 90.0,
            "breakout_20d": True,
            "veto_reason_codes": ["risk"],
        },
    ]

    gate_flow = summarize_ultra_gate_flow(_MODULE.pd.DataFrame(rows))

    assert gate_flow == {
        "window_feature_rows": 6,
        "pass_no_veto": 5,
        "pass_breakout_20d": 5,
        "pass_1h_range": 4,
        "pass_4h_range": 4,
        "pass_24h_momentum": 4,
        "pass_30d_return": 4,
        "pass_volume_ratio_24h_range": 4,
        "pass_top_24h_rank_gate": 3,
        "pass_7d_strength_gate": 2,
        "pass_30d_strength_gate": 1,
        "pass_quality_gate": 1,
    }
