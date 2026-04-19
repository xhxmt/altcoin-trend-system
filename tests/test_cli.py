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
    assert "backtest" in result.output


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


def test_cli_bootstrap_reports_full_market_selection_mode(monkeypatch):
    monkeypatch.setattr(
        "altcoin_trend.cli.load_settings",
        lambda: AppSettings(default_exchanges="binance", symbol_blocklist="BTCUSDT,ETHUSDT"),
    )
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.bootstrap_exchange",
        lambda adapter, engine, settings, lookback_days, now: type(
            "Result",
            (),
            {
                "exchange": adapter.exchange,
                "instruments_selected": 1,
                "bars_written": 2,
            },
        )(),
    )

    result = CliRunner().invoke(app, ["bootstrap", "--lookback-days", "30"])

    assert result.exit_code == 0
    assert "Bootstrap selection mode=full-market allowlist=0 blocklist=2" in result.output


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
            {
                "rank": 1,
                "exchange": "binance",
                "symbol": "SOLUSDT",
                "final_score": 88.4,
                "tier": "strong",
            }
        ],
    )

    result = CliRunner().invoke(app, ["rank", "--exchange", "bybit", "--limit", "5"])

    assert result.exit_code == 0
    assert "scope=bybit" in result.output
    assert "limit=5" in result.output
    assert "aggregate_symbols=False" in result.output
    assert "1. binance:SOLUSDT score=88.4 tier=strong" in result.output


def test_cli_rank_can_aggregate_symbols(monkeypatch):
    captured = {}
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings(default_exchanges="binance,bybit"))
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    def fake_load_rank_rows(engine, rank_scope, limit):
        captured["limit"] = limit
        return [
            {
                "rank": 1,
                "exchange": "bybit",
                "symbol": "SOLUSDT",
                "final_score": 91.2,
                "tier": "strong",
            },
            {
                "rank": 2,
                "exchange": "binance",
                "symbol": "SOLUSDT",
                "final_score": 88.4,
                "tier": "strong",
            },
            {
                "rank": 3,
                "exchange": "bybit",
                "symbol": "ETHUSDT",
                "final_score": 90.0,
                "tier": "strong",
            },
            {
                "rank": 4,
                "exchange": "binance",
                "symbol": "ETHUSDT",
                "final_score": 79.5,
                "tier": "watchlist",
            },
        ]

    monkeypatch.setattr("altcoin_trend.cli.load_rank_rows", fake_load_rank_rows)

    result = CliRunner().invoke(app, ["rank", "--limit", "1", "--aggregate-symbols"])

    assert result.exit_code == 0
    assert captured["limit"] > 1
    assert "aggregate_symbols=True" in result.output
    assert "1. bybit:SOLUSDT score=91.2 tier=strong exchanges=2 avg_score=89.8" in result.output
    assert "2." not in result.output


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


def test_cli_alerts_processes_pending_alerts(monkeypatch):
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings(alert_cooldown_seconds=3600))
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.process_alerts",
        lambda engine, now, cooldown_seconds, telegram_client: (2, 0),
    )

    result = CliRunner().invoke(app, ["alerts", "--since", "1h"])

    assert result.exit_code == 0
    assert "Alerts processed inserted=2 sent=0 since=1h" in result.output


def test_cli_bootstrap_derivatives_uses_loaded_settings(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "altcoin_trend.cli.load_settings",
        lambda: AppSettings(default_exchanges="binance,bybit", quote_asset="USDT"),
    )
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.bootstrap_derivatives",
        lambda adapter, engine, settings, lookback_days, now: calls.append((adapter.exchange, lookback_days)) or 7,
    )

    result = CliRunner().invoke(app, ["bootstrap-derivatives", "--lookback-days", "31"])

    assert result.exit_code == 0
    assert calls == [("binance", 31), ("bybit", 31)]
    assert "Derivatives bootstrap binance updates=7" in result.output
    assert "Derivatives bootstrap bybit updates=7" in result.output


def test_cli_bootstrap_derivatives_reports_allowlist_selection_mode(monkeypatch):
    monkeypatch.setattr(
        "altcoin_trend.cli.load_settings",
        lambda: AppSettings(
            default_exchanges="bybit",
            symbol_allowlist="SOLUSDT,ETHUSDT",
            symbol_blocklist="BTCUSDT",
        ),
    )
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr("altcoin_trend.cli.bootstrap_derivatives", lambda adapter, engine, settings, lookback_days, now: 7)

    result = CliRunner().invoke(app, ["bootstrap-derivatives", "--lookback-days", "31"])

    assert result.exit_code == 0
    assert "Bootstrap derivatives selection mode=allowlist allowlist=2 blocklist=1" in result.output


def test_cli_backtest_prints_summary(monkeypatch):
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.run_signal_backtest",
        lambda engine, start, end, min_score, horizons, high_value_only, limit: type(
            "Summary",
            (),
            {
                "signal_count": 2,
                "average_score": 85.0,
                "tier_counts": {"strong": 1, "watchlist": 1},
                "exchange_counts": {"binance": 1, "bybit": 1},
                "horizon_stats": {
                    "1h": type("Stats", (), {"avg_return": 0.025, "win_rate": 50.0})(),
                    "4h": type("Stats", (), {"avg_return": 0.25, "win_rate": 100.0})(),
                },
                "top_signals": [
                    {"exchange": "binance", "symbol": "SOLUSDT", "final_score": 90.0, "tier": "strong"},
                    {"exchange": "bybit", "symbol": "ETHUSDT", "final_score": 80.0, "tier": "watchlist"},
                ],
            },
        )(),
    )

    result = CliRunner().invoke(
        app,
        [
            "backtest",
            "--from",
            "2026-01-01T00:00:00",
            "--to",
            "2026-01-03T00:00:00",
            "--min-score",
            "80",
            "--horizons",
            "1h,4h",
            "--high-value-only",
            "--limit",
            "2",
        ],
    )

    assert result.exit_code == 0
    assert "signals=2" in result.output
    assert "average_score=85.0" in result.output
    assert "Tier counts: strong=1 watchlist=1" in result.output
    assert "Exchange counts: binance=1 bybit=1" in result.output
    assert "1h avg_return=2.50% win_rate=50.00%" in result.output
    assert "4h avg_return=25.00% win_rate=100.00%" in result.output
    assert "1. binance:SOLUSDT score=90.0 tier=strong" in result.output
