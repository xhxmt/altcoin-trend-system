from typer.testing import CliRunner

from altcoin_trend.cli import app
from altcoin_trend.config import AppSettings


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
    monkeypatch.setattr(
        "altcoin_trend.cli.load_settings",
        lambda: AppSettings(default_exchanges="binance,bybit", quote_asset="USDT"),
    )

    result = CliRunner().invoke(app, ["bootstrap", "--lookback-days", "30"])

    assert result.exit_code == 0
    assert "Bootstrap requested lookback_days=30 exchanges=binance,bybit quote=USDT" in result.output
