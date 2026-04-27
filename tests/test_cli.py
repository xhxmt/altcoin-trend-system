from datetime import datetime, timedelta, timezone

from typer.testing import CliRunner

from altcoin_trend.cli import app
from altcoin_trend.config import AppSettings
from altcoin_trend.scheduler import RunOnceResult


def test_cli_help_lists_mvp_commands():
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "init-db" in result.output
    assert "bootstrap" in result.output
    assert "sync-once" in result.output
    assert "run-once" in result.output
    assert "daemon" in result.output
    assert "rank" in result.output
    assert "status" in result.output
    assert "alerts" in result.output
    assert "explain" in result.output
    assert "backtest" in result.output
    assert "trade-candidates" in result.output
    assert "evaluate-trade-candidates" in result.output
    assert "health" in result.output


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
        lambda: AppSettings(default_exchanges="binance", symbol_allowlist="", symbol_blocklist="BTCUSDT,ETHUSDT"),
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


def test_evaluate_signals_v2_command_prints_group_summary(monkeypatch):
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.run_signal_v2_backtest",
        lambda engine, exchange, start, end: {
            "continuation_A": {
                "signal_count": 2,
                "hit_10pct_before_drawdown_8pct_rate": 50.0,
                "avg_mfe_1h_pct": 12.5,
                "avg_mae_1h_pct": 1.25,
            }
        },
    )

    result = CliRunner().invoke(
        app,
        ["evaluate-signals-v2", "--from", "2026-01-01", "--to", "2026-01-02", "--exchange", "binance"],
    )

    assert result.exit_code == 0
    assert "Signal v2 backtest exchange=binance from=2026-01-01T00:00:00+00:00 to=2026-01-02T00:00:00+00:00" in result.output
    assert "continuation_A signals=2 hit10_before_dd8=50.00%" in result.output


def test_cli_explain_uses_requested_exchange_and_symbol(monkeypatch):
    captured = {}
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())

    def fake_load_explain_row(engine, symbol, exchange, at=None):
        captured["symbol"] = symbol
        captured["exchange"] = exchange
        captured["at"] = at
        return None

    monkeypatch.setattr("altcoin_trend.cli.load_explain_row", fake_load_explain_row)

    result = CliRunner().invoke(app, ["explain", "solusdt", "--exchange", "binance"])

    assert result.exit_code == 0
    assert "binance:SOLUSDT" in result.output
    assert captured["symbol"] == "solusdt"
    assert captured["exchange"] == "binance"
    assert captured["at"] is None


def test_cli_explain_accepts_historical_snapshot_time(monkeypatch):
    captured = {}
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())

    def fake_load_explain_row(engine, symbol, exchange, at=None):
        captured["at"] = at
        return None

    monkeypatch.setattr("altcoin_trend.cli.load_explain_row", fake_load_explain_row)

    result = CliRunner().invoke(
        app,
        ["explain", "solusdt", "--exchange", "binance", "--at", "2026-04-26T19:59:46Z"],
    )

    assert result.exit_code == 0
    assert captured["at"] == datetime(2026, 4, 26, 19, 59, 46, tzinfo=timezone.utc)


def test_cli_explain_prints_snapshot_when_available(monkeypatch):
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.load_explain_row",
        lambda engine, symbol, exchange, at=None: {
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


def test_opportunities_command_prints_actionability_rows(monkeypatch):
    from typer.testing import CliRunner

    from altcoin_trend.cli import app

    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: object())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.load_opportunity_rows",
        lambda engine, limit: [
            {
                "exchange": "binance",
                "symbol": "RAVEUSDT",
                "actionability_score": 68.5,
                "signal_priority": 3,
                "continuation_grade": None,
                "ignition_grade": "EXTREME",
                "chase_risk_score": 80.0,
                "final_score": 63.0,
            }
        ],
    )

    result = CliRunner().invoke(app, ["opportunities", "--limit", "5"])

    assert result.exit_code == 0
    assert "RAVEUSDT" in result.output
    assert "actionability=68.5" in result.output
    assert "ignition=EXTREME" in result.output


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


def test_cli_sync_once_reports_input_sync_status(monkeypatch):
    monkeypatch.setattr(
        "altcoin_trend.cli.load_settings",
        lambda: AppSettings(symbol_allowlist="SOLUSDT"),
    )
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.sync_market_inputs",
        lambda *, engine, settings, now: type(
            "Result",
            (),
            {
                "status": "healthy",
                "message": "instruments_selected=2 bars_written=4 derivatives_updated=1",
            },
        )(),
    )

    result = CliRunner().invoke(app, ["sync-once"])

    assert result.exit_code == 0
    assert "Sync once status=healthy message=instruments_selected=2 bars_written=4 derivatives_updated=1" in result.output


def test_cli_status_reports_loaded_settings(monkeypatch):
    monkeypatch.setattr(
        "altcoin_trend.cli.load_settings",
        lambda: AppSettings(default_exchanges="binance,bybit", signal_interval_seconds=90),
    )

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Status: configured exchanges=binance,bybit interval=90s" in result.output


def test_cli_health_prints_health_report(monkeypatch):
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr("altcoin_trend.cli.collect_health", lambda engine: "health text")

    result = CliRunner().invoke(app, ["health"])

    assert result.exit_code == 0
    assert result.output == "health text\n"


def test_cli_alerts_processes_pending_alerts(monkeypatch):
    captured = {}
    now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings(alert_cooldown_seconds=3600))
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr("altcoin_trend.cli._utc_now", lambda: now)

    def fake_process_alerts(engine, now, cooldown_seconds, telegram_client, recent_since):
        captured["now"] = now
        captured["recent_since"] = recent_since
        return 2, 0

    monkeypatch.setattr("altcoin_trend.cli.process_alerts", fake_process_alerts)

    result = CliRunner().invoke(app, ["alerts", "--since", "1h"])

    assert result.exit_code == 0
    assert captured["now"] == now
    assert captured["recent_since"] == datetime(2026, 4, 23, 11, 0, tzinfo=timezone.utc)
    assert "Alerts processed inserted=2 sent=0 since=2026-04-23T11:00:00+00:00" in result.output


def test_cli_alerts_normalizes_supported_since_values(monkeypatch):
    captured = []
    now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings(alert_cooldown_seconds=3600))
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr("altcoin_trend.cli._utc_now", lambda: now)

    def fake_process_alerts(engine, now, cooldown_seconds, telegram_client, recent_since):
        captured.append(recent_since)
        return 0, 0

    monkeypatch.setattr("altcoin_trend.cli.process_alerts", fake_process_alerts)

    cases = [
        ("30m", now - timedelta(minutes=30)),
        ("1h", now - timedelta(hours=1)),
        ("7d", now - timedelta(days=7)),
        ("2026-04-23T08:00:00Z", datetime(2026, 4, 23, 8, 0, tzinfo=timezone.utc)),
        ("2026-04-23T08:00:00", datetime(2026, 4, 23, 8, 0, tzinfo=timezone.utc)),
    ]
    for value, expected in cases:
        result = CliRunner().invoke(app, ["alerts", "--since", value])
        assert result.exit_code == 0
        assert captured[-1] == expected


def test_cli_alerts_rejects_invalid_or_future_since(monkeypatch):
    now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings(alert_cooldown_seconds=3600))
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr("altcoin_trend.cli._utc_now", lambda: now)
    monkeypatch.setattr("altcoin_trend.cli.process_alerts", lambda **kwargs: (0, 0))

    invalid = CliRunner().invoke(app, ["alerts", "--since", "not-a-window"])
    assert invalid.exit_code != 0
    assert "--since must be a relative duration" in invalid.output
    assert "ISO datetime" in invalid.output

    future = CliRunner().invoke(app, ["alerts", "--since", "2026-04-23T12:01:00Z"])
    assert future.exit_code != 0
    assert "must not be in the future" in future.output


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
    captured = {}
    def fake_run_signal_backtest(engine, start, end, min_score, horizons, high_value_only, limit):
        captured["min_score"] = min_score
        return type(
            "Summary",
            (),
            {
                "signal_count": 2,
                "average_score": 85.0,
                "tier_counts": {"strong": 1, "watchlist": 1},
                "exchange_counts": {"binance": 1, "bybit": 1},
                "horizon_stats": {
                    "1h": type("Stats", (), {"avg_return": 0.025, "win_rate": 50.0, "observations": 2})(),
                    "4h": type("Stats", (), {"avg_return": 0.25, "win_rate": 100.0, "observations": 1})(),
                },
                "top_signals": [
                    {"exchange": "binance", "symbol": "SOLUSDT", "final_score": 90.0, "tier": "strong"},
                    {"exchange": "bybit", "symbol": "ETHUSDT", "final_score": 80.0, "tier": "watchlist"},
                ],
            },
        )()

    monkeypatch.setattr("altcoin_trend.cli.run_signal_backtest", fake_run_signal_backtest)

    result = CliRunner().invoke(
        app,
        [
            "backtest",
            "--from",
            "2026-01-01T00:00:00",
            "--to",
            "2026-01-03T00:00:00",
            "--horizons",
            "1h,4h",
            "--high-value-only",
            "--limit",
            "2",
        ],
    )

    assert result.exit_code == 0
    assert captured["min_score"] == 60.0
    assert "signals=2" in result.output
    assert "average_score=85.0" in result.output
    assert "Tier counts: strong=1 watchlist=1" in result.output
    assert "Exchange counts: binance=1 bybit=1" in result.output
    assert "1h avg_return=2.50% win_rate=50.00% observations=2" in result.output
    assert "4h avg_return=25.00% win_rate=100.00% observations=1" in result.output
    assert "1. binance:SOLUSDT score=90.0 tier=strong" in result.output


def test_cli_backtest_invalid_horizons_reports_bad_parameter(monkeypatch):
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())

    result = CliRunner().invoke(
        app,
        [
            "backtest",
            "--from",
            "2026-01-01T00:00:00",
            "--to",
            "2026-01-03T00:00:00",
            "--horizons",
            "1h,broken",
        ],
    )

    assert result.exit_code != 0
    assert "Unsupported horizon: broken" in result.output
