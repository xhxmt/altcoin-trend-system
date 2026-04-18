from datetime import datetime, timezone

from typer.testing import CliRunner

from altcoin_trend.cli import app
from altcoin_trend.config import AppSettings
from altcoin_trend.scheduler import RunOnceResult


def test_cli_help_lists_mvp_commands():
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "init-db" in result.output
    assert "bootstrap" in result.output
    assert "run-once" in result.output
    assert "daemon" in result.output
    assert "rank" in result.output
    assert "status" in result.output
    assert "alerts" in result.output
    assert "explain" in result.output


def test_cli_bootstrap_uses_loaded_settings(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "altcoin_trend.cli.load_settings",
        lambda: AppSettings(default_exchanges="binance,bybit", quote_asset="USDT"),
    )
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())

    def fake_bootstrap_exchange(adapter, engine, settings, lookback_days, now):
        calls.append((adapter.exchange, lookback_days))

        class Result:
            exchange = adapter.exchange
            instruments_selected = 2
            bars_written = 3

        return Result()

    monkeypatch.setattr("altcoin_trend.cli.bootstrap_exchange", fake_bootstrap_exchange)

    result = CliRunner().invoke(app, ["bootstrap", "--lookback-days", "30"])

    assert result.exit_code == 0
    assert calls == [("binance", 30), ("bybit", 30)]
    assert "Bootstrap binance instruments=2 bars_written=3" in result.output
    assert "Bootstrap bybit instruments=2 bars_written=3" in result.output
    assert "Bootstrap completed exchanges=2 bars_written=6" in result.output


def test_cli_explain_uses_requested_exchange_and_symbol(monkeypatch):
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr("altcoin_trend.cli.load_explain_row", lambda engine, symbol, exchange: None)

    result = CliRunner().invoke(app, ["explain", "solusdt", "--exchange", "binance"])

    assert result.exit_code == 0
    assert "binance:SOLUSDT" in result.output


def test_cli_explain_prints_snapshot_when_available(monkeypatch):
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.load_explain_row",
        lambda engine, symbol, exchange: {
            "exchange": exchange,
            "symbol": symbol.upper(),
            "final_score": 88.4,
            "tier": "strong",
            "trend_score": 80.0,
            "volume_breakout_score": 90.0,
            "relative_strength_score": 50.0,
            "derivatives_score": 50.0,
            "quality_score": 100.0,
            "veto_reason_codes": [],
        },
    )

    result = CliRunner().invoke(app, ["explain", "solusdt", "--exchange", "binance"])

    assert result.exit_code == 0
    assert "binance:SOLUSDT" in result.output
    assert "Score: 88.4" in result.output


def test_cli_rank_echoes_scope_and_limit(monkeypatch):
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.load_rank_rows",
        lambda engine, rank_scope, limit: [
            {"rank": 1, "symbol": "SOLUSDT", "final_score": 88.4, "tier": "strong"}
        ],
    )

    result = CliRunner().invoke(app, ["rank", "--exchange", "bybit", "--limit", "5"])

    assert result.exit_code == 0
    assert "scope=bybit" in result.output
    assert "limit=5" in result.output
    assert "1. SOLUSDT score=88.4 tier=strong" in result.output


def test_cli_run_once_reports_pipeline_status(monkeypatch):
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.run_once_pipeline",
        lambda engine: RunOnceResult(datetime(2024, 1, 1, tzinfo=timezone.utc), "healthy", "ok"),
    )

    result = CliRunner().invoke(app, ["run-once"])

    assert result.exit_code == 0
    assert "Run once status=healthy message=ok" in result.output


def test_cli_status_reports_loaded_settings(monkeypatch):
    monkeypatch.setattr(
        "altcoin_trend.cli.load_settings",
        lambda: AppSettings(default_exchanges="binance,bybit", signal_interval_seconds=90),
    )

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Status: configured exchanges=binance,bybit interval=90s" in result.output
