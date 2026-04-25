import csv
import json
import importlib.util
import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace
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
SIGNALS_MINIMUM_COLUMNS = _MODULE.SIGNALS_MINIMUM_COLUMNS
build_run_metadata = _MODULE.build_run_metadata
summarize_ultra_gate_flow = _MODULE.summarize_ultra_gate_flow
summarize_evaluated_signals = _MODULE.summarize_evaluated_signals
write_artifacts = _MODULE.write_artifacts


def test_main_passes_resolved_git_sha_to_metadata(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    summary = {
        "exchange": "binance",
        "from": "2026-01-22T10:00:00+00:00",
        "to": "2026-04-22T10:00:00+00:00",
        "market_from": "2025-12-22T10:00:00+00:00",
        "market_to": "2026-04-23T11:00:00+00:00",
        "coverage_status": "trusted",
        "primary_label_complete_count": 0,
        "incomplete_label_count": 0,
        "missing_optional_columns": [],
    }

    def fake_build_run_metadata(**kwargs):
        captured["git_sha"] = kwargs.get("git_sha")
        captured["symbol_allowlist"] = kwargs.get("symbol_allowlist")
        captured["symbol_blocklist"] = kwargs.get("symbol_blocklist")
        return {
            "generated_at": "2026-04-23T12:06:03+00:00",
            "validation_window": {"from": summary["from"], "to": summary["to"]},
            "warmup_window": {"from": summary["market_from"], "to": summary["from"]},
            "forward_window": {"from": summary["to"], "to": summary["market_to"], "horizon": "24h"},
            "expected_inputs": {"required_features": []},
            "expected_outputs": {
                "summary": SUMMARY_FILENAME,
                "signals": SIGNALS_FILENAME,
                "metadata": METADATA_FILENAME,
                "readme": README_FILENAME,
            },
            "signal_family": "ultra_high_conviction",
            "exchange": "binance",
        }

    monkeypatch.setattr(_MODULE, "_current_git_sha", lambda: "dirty:abc123", raising=False)
    monkeypatch.setattr(
        _MODULE,
        "load_settings",
        lambda: SimpleNamespace(symbol_allowlist="SOLUSDT, ethusdt ", symbol_blocklist=" BTCUSDT "),
    )
    monkeypatch.setattr(_MODULE, "build_engine", lambda settings: object())
    monkeypatch.setattr(_MODULE, "evaluate_signal_family", lambda *args, **kwargs: (summary, []))
    monkeypatch.setattr(_MODULE, "build_run_metadata", fake_build_run_metadata)
    monkeypatch.setattr(_MODULE, "write_artifacts", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        _MODULE.sys,
        "argv",
        [
            "validate_ultra_signal_production.py",
            "--from",
            "2026-01-22T10:00:00+00:00",
            "--to",
            "2026-04-22T10:00:00+00:00",
            "--output-root",
            str(tmp_path),
        ],
    )

    assert _MODULE.main() == 0
    assert captured["git_sha"] == "dirty:abc123"
    assert captured["symbol_allowlist"] == ["SOLUSDT", "ethusdt"]
    assert captured["symbol_blocklist"] == ["BTCUSDT"]


def test_current_git_sha_marks_dirty_worktree(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command == ["git", "rev-parse", "--short", "HEAD"]:
            return type("Result", (), {"stdout": "abc123\n"})()
        if command == ["git", "status", "--porcelain"]:
            return type("Result", (), {"stdout": " M scripts/validate_ultra_signal_production.py\n"})()
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(_MODULE.subprocess, "run", fake_run)

    assert _MODULE._current_git_sha(tmp_path) == "dirty:abc123"
    assert calls == [["git", "rev-parse", "--short", "HEAD"], ["git", "status", "--porcelain"]]


def test_current_git_sha_returns_clean_sha(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        if command == ["git", "rev-parse", "--short", "HEAD"]:
            return type("Result", (), {"stdout": "abc123\n"})()
        if command == ["git", "status", "--porcelain"]:
            return type("Result", (), {"stdout": ""})()
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(_MODULE.subprocess, "run", fake_run)

    assert _MODULE._current_git_sha(tmp_path) == "abc123"


def test_current_git_sha_returns_unknown_on_failure(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        raise subprocess.CalledProcessError(returncode=128, cmd=command)

    monkeypatch.setattr(_MODULE.subprocess, "run", fake_run)

    assert _MODULE._current_git_sha(tmp_path) == "unknown"


def test_empty_hourly_summary_preserves_market_window_and_artifact_flow(monkeypatch, tmp_path):
    monkeypatch.setattr(_MODULE, "fetch_hourly_bars", lambda *args, **kwargs: _MODULE.pd.DataFrame())

    start = datetime(2026, 1, 22, 10, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 22, 10, 0, tzinfo=timezone.utc)
    summary, rows = _MODULE.evaluate_signal_family(object(), "binance", start, end)

    assert rows == []
    assert summary["market_from"] == "2025-12-22T10:00:00+00:00"
    assert summary["market_to"] == "2026-04-23T11:00:00+00:00"
    metadata = build_run_metadata(
        exchange="binance",
        start=start,
        end=end,
        market_start=datetime.fromisoformat(summary["market_from"]),
        market_end=datetime.fromisoformat(summary["market_to"]),
        output_dir=tmp_path / "run",
        output_root=tmp_path,
        coverage_status=str(summary["coverage_status"]),
        primary_label_complete_count=int(summary["primary_label_complete_count"]),
        incomplete_label_count=int(summary["incomplete_label_count"]),
        missing_optional_columns=list(summary["missing_optional_columns"]),
    )

    write_artifacts(tmp_path / "run", summary, rows, metadata)
    assert (tmp_path / "run" / SIGNALS_FILENAME).read_text(encoding="utf-8").splitlines() == [
        ",".join(SIGNALS_MINIMUM_COLUMNS)
    ]


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
        coverage_status="trusted",
        primary_label_complete_count=4,
        incomplete_label_count=0,
    )

    assert metadata["validator_version"] == "v1.1"
    assert metadata["entry_policy"] == "hour_close_proxy"
    assert metadata["market_1m_timestamp_semantics"] == "minute_open_utc"
    assert metadata["timestamp_semantics"] == "hour_bucket_start_utc"
    assert metadata["forward_scan_start_policy"] == "signal_available_at_inclusive"
    assert metadata["primary_label"] == "+10_before_-8"
    assert metadata["horizon_hours"] == 24
    assert metadata["coverage_status"] == "trusted"
    assert metadata["feature_preparation_version"] == "feature_v2"
    assert metadata["rule_version"].startswith("ultra_high_conviction:sha256:")
    assert metadata["rule_config_hash"].startswith("sha256:")
    assert isinstance(metadata["rule_config"], dict)
    assert metadata["validation_window"] == {
        "from": "2026-01-22T10:00:00+00:00",
        "to": "2026-04-22T10:00:00+00:00",
    }
    assert metadata["symbol_allowlist"] == []
    assert metadata["symbol_blocklist"] == []
    assert metadata["missing_optional_columns"] == []
    assert metadata["signal_family"] == "ultra_high_conviction"
    assert metadata["warmup_window"]["from"] == "2025-12-22T10:00:00+00:00"
    assert metadata["forward_window"]["to"] == "2026-04-23T11:00:00+00:00"
    assert metadata["expected_outputs"]["summary"] == SUMMARY_FILENAME
    assert metadata["expected_outputs"]["signal_identity_columns"] == ["exchange", "symbol", "ts", "asset_id"]
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
        "hit_10pct_first_rate": 0.5,
        "drawdown_8pct_first_rate": 0.25,
        "avg_mfe_24h_pct": 42.0,
        "avg_mae_24h_pct": 18.0,
        "avg_mfe_before_dd8_pct": 21.0,
        "avg_mae_before_hit_10pct": 7.25,
        "avg_mae_after_hit_10pct": 5.75,
        "median_time_to_hit_10pct_minutes": 30.0,
        "median_time_to_drawdown_8pct_minutes": 45.0,
    }
    rows = [
        {
            "exchange": "binance",
            "symbol": "HIGHUSDT",
            "signal_family": "ultra_high_conviction",
            "signal_ts": "2026-04-22T10:00:00+00:00",
            "signal_available_at": "2026-04-22T11:00:00+00:00",
            "entry_ts": "2026-04-22T11:00:00+00:00",
            "entry_price": 100.0,
            "label_complete_24h": True,
            "hit_10_before_dd8": True,
            "mfe_24h_pct": 12.0,
            "mae_24h_pct": -4.0,
            "abs_mae_24h_pct": 4.0,
            "time_to_hit_10pct_minutes": 12.0,
            "path_order": "target_first",
            "z_extra": "last",
            "a_extra": "first",
            "path_results": {"target_15_dd_12": {"hit": False}, "target_5_dd_8": {"hit": True}},
        }
    ]
    metadata = build_run_metadata(
        exchange="binance",
        start=datetime(2026, 1, 22, 10, 0, tzinfo=timezone.utc),
        end=datetime(2026, 4, 22, 10, 0, tzinfo=timezone.utc),
        market_start=datetime(2025, 12, 22, 10, 0, tzinfo=timezone.utc),
        market_end=datetime(2026, 4, 23, 11, 0, tzinfo=timezone.utc),
        output_dir=output_dir,
        output_root=tmp_path,
        generated_at=datetime(2026, 4, 23, 12, 6, 3, tzinfo=timezone.utc),
        coverage_status="trusted",
        primary_label_complete_count=4,
        incomplete_label_count=0,
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
    assert "avg_mae_before_hit_10pct: 7.25" in readme
    assert "pass_rank_24h: 0" in readme
    assert "pass_quality_gate: 4" in readme
    assert "signal_family: ultra_high_conviction" in readme
    assert "validator_version: v1.1" in readme
    assert "entry_policy: hour_close_proxy" in readme
    assert "forward_scan_start_policy: signal_available_at_inclusive" in readme
    assert "hit10_24h_rate" in readme
    assert "avg_abs_mae_24h_pct" in readme
    signals_lines = (output_dir / SIGNALS_FILENAME).read_text(encoding="utf-8").splitlines()
    header = signals_lines[0].split(",")
    assert header[: len(SIGNALS_MINIMUM_COLUMNS)] == SIGNALS_MINIMUM_COLUMNS
    assert header[len(SIGNALS_MINIMUM_COLUMNS) :] == ["a_extra", "path_results_json", "z_extra"]
    assert "path_results" not in header
    record = next(csv.DictReader(signals_lines))
    assert record["signal_grade"] == ""
    assert record["entry_policy"] == ""
    assert record["a_extra"] == "first"
    assert record["z_extra"] == "last"
    assert record["path_results_json"] == json.dumps(rows[0]["path_results"], sort_keys=True)


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
        "hit_10pct_first_rate": 0.0,
        "drawdown_8pct_first_rate": 0.0,
        "avg_mfe_24h_pct": 0.0,
        "avg_mae_24h_pct": 0.0,
        "avg_mfe_before_dd8_pct": 0.0,
        "avg_mae_before_hit_10pct": 0.0,
        "avg_mae_after_hit_10pct": 0.0,
        "median_time_to_hit_10pct_minutes": 0.0,
        "median_time_to_drawdown_8pct_minutes": 0.0,
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
        coverage_status="trusted",
        primary_label_complete_count=4,
        incomplete_label_count=0,
    )

    write_artifacts(output_dir, summary, [], metadata)

    signals_lines = (output_dir / SIGNALS_FILENAME).read_text(encoding="utf-8").splitlines()
    assert signals_lines == [",".join(SIGNALS_MINIMUM_COLUMNS)]


def test_summarize_ultra_gate_flow_counts_cumulative_stage_passes():
    rows = [
        {
            "return_1h_pct": 15.0,
            "return_4h_pct": 50.0,
            "return_24h_pct": 90.0,
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
            "return_24h_pct": 90.0,
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
            "return_24h_pct": 90.0,
            "return_30d_pct": 70.0,
            "volume_ratio_24h": 6.0,
            "return_24h_rank": 3,
            "return_24h_percentile": 0.99,
            "return_7d_percentile": 0.97,
            "return_30d_percentile": 0.85,
            "quality_score": 90.0,
            "breakout_20d": True,
            "veto_reason_codes": [],
        },
        {
            "return_1h_pct": 15.0,
            "return_4h_pct": 50.0,
            "return_24h_pct": 90.0,
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
            "return_24h_pct": 90.0,
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
            "return_24h_pct": 90.0,
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
        "pass_20d_breakout": 5,
        "pass_breakout_20d": 5,
        "pass_min_return_1h": 5,
        "pass_max_return_1h": 4,
        "pass_1h_range": 4,
        "pass_min_return_4h": 4,
        "pass_max_return_4h": 4,
        "pass_4h_range": 4,
        "pass_min_return_24h": 4,
        "pass_24h_momentum": 4,
        "pass_min_return_30d": 4,
        "pass_30d_return": 4,
        "pass_min_volume_ratio_24h": 4,
        "pass_max_volume_ratio_24h": 4,
        "pass_volume_ratio_24h_range": 4,
        "pass_rank_24h": 3,
        "pass_top_24h_rank_gate": 3,
        "pass_rs_7d": 2,
        "pass_7d_strength_gate": 2,
        "pass_rs_30d": 1,
        "pass_30d_strength_gate": 1,
        "pass_quality_gate": 1,
        "final_ultra_signal_count": 1,
    }


def test_summarize_evaluated_signals_reports_path_risk_metrics():
    summary = summarize_evaluated_signals(
        [
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
                "mfe_1h_pct": 12.0,
                "mfe_24h_pct": 30.0,
                "mae_24h_pct": -5.0,
                "abs_mae_24h_pct": 5.0,
                "mfe_before_dd8_pct": 15.0,
                "mae_before_hit_10pct": 2.0,
                "mae_after_hit_10pct": 4.0,
                "time_to_hit_10pct_minutes": 20.0,
                "time_to_drawdown_8pct_minutes": 60.0,
            },
            {
                "label_complete_1h": True,
                "label_complete_4h": True,
                "label_complete_24h": True,
                "hit_10pct_1h": False,
                "hit_10pct_4h": True,
                "hit_10pct_24h": True,
                "hit_10pct_before_drawdown_8pct": False,
                "hit_10pct_first": False,
                "drawdown_8pct_first": True,
                "mfe_1h_pct": 8.0,
                "mfe_24h_pct": 40.0,
                "mae_24h_pct": -12.0,
                "abs_mae_24h_pct": 12.0,
                "mfe_before_dd8_pct": 10.0,
                "mae_before_hit_10pct": 9.0,
                "mae_after_hit_10pct": 1.0,
                "time_to_hit_10pct_minutes": None,
                "time_to_drawdown_8pct_minutes": 10.0,
            },
            {
                "label_complete_1h": True,
                "label_complete_4h": True,
                "label_complete_24h": True,
                "hit_10pct_1h": False,
                "hit_10pct_4h": False,
                "hit_10pct_24h": False,
                "hit_10pct_before_drawdown_8pct": False,
                "hit_10pct_first": None,
                "drawdown_8pct_first": None,
                "mfe_1h_pct": 1.0,
                "mfe_24h_pct": 6.0,
                "mae_24h_pct": -4.0,
                "abs_mae_24h_pct": 4.0,
                "mfe_before_dd8_pct": 6.0,
                "mae_before_hit_10pct": 3.0,
                "mae_after_hit_10pct": None,
                "time_to_hit_10pct_minutes": None,
                "time_to_drawdown_8pct_minutes": None,
            },
        ]
    )

    assert summary["ultra_signal_count"] == 3
    assert summary["hit_10_24h_count"] == 2
    assert summary["hit_10_before_dd8_count"] == 1
    assert summary["hit_10pct_first_count"] == 1
    assert summary["drawdown_8pct_first_count"] == 1
    assert summary["unresolved_24h_count"] == 1
    assert summary["hit_10pct_first_rate"] == 0.333333
    assert summary["drawdown_8pct_first_rate"] == 0.333333
    assert summary["hit10_24h_rate"] == 0.666667
    assert summary["avg_mae_24h_pct"] == -7.0
    assert summary["avg_abs_mae_24h_pct"] == 7.0
    assert summary["avg_mfe_before_dd8_pct"] == 10.333333
    assert summary["avg_mae_before_hit_10pct"] == 4.666667
    assert summary["avg_mae_after_hit_10pct"] == 2.5
    assert summary["median_time_to_hit_10pct_minutes"] == 20.0
    assert summary["median_time_to_drawdown_8pct_minutes"] == 35.0


def test_build_run_metadata_supports_ignition_family(tmp_path):
    output_dir = tmp_path / "20260424-070000-production-ignition-binance"
    metadata = build_run_metadata(
        exchange="binance",
        start=datetime(2026, 3, 23, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 4, 22, 0, 0, tzinfo=timezone.utc),
        market_start=datetime(2026, 2, 20, 0, 0, tzinfo=timezone.utc),
        market_end=datetime(2026, 4, 23, 1, 0, tzinfo=timezone.utc),
        output_dir=output_dir,
        output_root=tmp_path,
        signal_family="ignition",
        generated_at=datetime(2026, 4, 24, 7, 0, 0, tzinfo=timezone.utc),
        coverage_status="trusted",
        primary_label_complete_count=4,
        incomplete_label_count=0,
    )

    assert metadata["signal_family"] == "ignition"
    assert metadata["signal_family_slug"] == "ignition"
    assert "ignition_grade" in metadata["expected_inputs"]["required_features"]
    assert "ultra_high_conviction" not in metadata["expected_inputs"]["required_features"]


def test_build_run_readme_includes_ignition_group_snapshot(tmp_path):
    output_dir = tmp_path / "run"
    summary = {
        "signal_family": "ignition",
        "signal_count": 6,
        "ignition_signal_count": 6,
        "precision_1h": 0.5,
        "precision_4h": 0.666667,
        "precision_24h": 0.833333,
        "precision_before_dd8": 0.333333,
        "hit_10pct_first_rate": 0.333333,
        "drawdown_8pct_first_rate": 0.5,
        "avg_mfe_24h_pct": 20.0,
        "avg_mae_24h_pct": 14.0,
        "avg_mfe_before_dd8_pct": 9.0,
        "avg_mae_before_hit_10pct": 7.0,
        "avg_mae_after_hit_10pct": 4.0,
        "median_time_to_hit_10pct_minutes": 45.0,
        "median_time_to_drawdown_8pct_minutes": 20.0,
        "group_summary": {
            "ignition_EXTREME": {"signal_count": 1, "hit_10pct_before_drawdown_8pct_rate": 100.0, "avg_mae_24h_pct": 3.0},
            "ignition_A": {"signal_count": 2, "hit_10pct_before_drawdown_8pct_rate": 50.0, "avg_mae_24h_pct": 8.0},
            "ignition_B": {"signal_count": 3, "hit_10pct_before_drawdown_8pct_rate": 0.0, "avg_mae_24h_pct": 19.0},
            "high_chase_risk": {"signal_count": 4, "hit_10pct_before_drawdown_8pct_rate": 25.0, "avg_mae_24h_pct": 16.0},
            "low_or_medium_chase_risk": {"signal_count": 2, "hit_10pct_before_drawdown_8pct_rate": 50.0, "avg_mae_24h_pct": 10.0},
        },
        "gate_flow": {},
    }
    metadata = build_run_metadata(
        exchange="binance",
        start=datetime(2026, 3, 23, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 4, 22, 0, 0, tzinfo=timezone.utc),
        market_start=datetime(2026, 2, 20, 0, 0, tzinfo=timezone.utc),
        market_end=datetime(2026, 4, 23, 1, 0, tzinfo=timezone.utc),
        output_dir=output_dir,
        output_root=tmp_path,
        signal_family="ignition",
        generated_at=datetime(2026, 4, 24, 7, 0, 0, tzinfo=timezone.utc),
        coverage_status="trusted",
        primary_label_complete_count=4,
        incomplete_label_count=0,
    )

    write_artifacts(output_dir, summary, [], metadata)
    readme = (output_dir / README_FILENAME).read_text(encoding="utf-8")
    assert "# Ignition Production Validation" in readme
    assert "signal_family: ignition" in readme
    assert "ignition_signal_count: 6" in readme
    assert "ignition_B: count=3, hit_10_before_dd8=0.000000, avg_mae_24h_pct=19.000000" in readme
    assert "high_chase_risk: count=4, hit_10_before_dd8=25.000000, avg_mae_24h_pct=16.000000" in readme


def test_summarize_evaluated_signals_adds_ignition_alias_count_key():
    summary = summarize_evaluated_signals(
        [
            {
                "hit_10pct_1h": True,
                "hit_10pct_4h": True,
                "hit_10pct_24h": True,
                "hit_10pct_before_drawdown_8pct": True,
                "hit_10pct_first": True,
                "drawdown_8pct_first": False,
                "mfe_1h_pct": 14.0,
                "mfe_24h_pct": 25.0,
                "mae_24h_pct": 4.0,
                "mfe_before_dd8_pct": 14.0,
                "mae_before_hit_10pct": 2.0,
                "mae_after_hit_10pct": 1.0,
                "time_to_hit_10pct_minutes": 15.0,
                "time_to_drawdown_8pct_minutes": None,
            }
        ],
        signal_family="ignition",
    )

    assert summary["signal_family"] == "ignition"
    assert summary["signal_count"] == 1
    assert summary["ignition_signal_count"] == 1
