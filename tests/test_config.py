from pathlib import Path

from altcoin_trend.config import AppSettings, load_settings


def test_settings_defaults_point_to_project_paths(monkeypatch):
    for key in (
        "ACTS_DATABASE_URL",
        "ACTS_OUTPUT_ROOT",
        "ACTS_DEFAULT_EXCHANGES",
        "ACTS_QUOTE_ASSET",
        "ACTS_MIN_QUOTE_VOLUME_24H",
        "ACTS_MIN_LISTING_DAYS",
        "ACTS_BOOTSTRAP_LOOKBACK_DAYS",
        "ACTS_SIGNAL_INTERVAL_SECONDS",
        "ACTS_ALERT_COOLDOWN_SECONDS",
        "ACTS_TELEGRAM_BOT_TOKEN",
        "ACTS_TELEGRAM_CHAT_ID",
        "ACTS_SYMBOL_ALLOWLIST",
        "ACTS_SYMBOL_BLOCKLIST",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = AppSettings()

    assert settings.default_exchanges == "binance,bybit"
    assert settings.exchanges == ("binance", "bybit")
    assert settings.quote_asset == "USDT"
    assert settings.min_quote_volume_24h == 5_000_000
    assert settings.signal_interval_seconds == 60
    assert settings.output_root == "/home/tfisher/altcoin-trend-system/artifacts"
    assert settings.artifacts_dir == Path(settings.output_root)
    assert settings.blocklist_symbols == set()


def test_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("ACTS_DATABASE_URL", "postgresql+psycopg://tester@/acts_test")
    monkeypatch.setenv("ACTS_SYMBOL_ALLOWLIST", "SOLUSDT,ARBUSDT")
    monkeypatch.setenv("ACTS_SYMBOL_BLOCKLIST", "  btcusdt , ethusdt ")

    settings = load_settings()

    assert settings.database_url == "postgresql+psycopg://tester@/acts_test"
    assert settings.symbol_allowlist == "SOLUSDT,ARBUSDT"
    assert settings.allowlist_symbols == {"SOLUSDT", "ARBUSDT"}
    assert settings.blocklist_symbols == {"BTCUSDT", "ETHUSDT"}


def test_settings_load_from_env_file_and_keep_env_override(tmp_path, monkeypatch):
    env_file = tmp_path / "acts.env"
    env_file.write_text(
        "\n".join(
            [
                "ACTS_DATABASE_URL=postgresql+psycopg://from_file/acts",
                "ACTS_OUTPUT_ROOT=/tmp/acts-artifacts",
                "ACTS_DEFAULT_EXCHANGES=kraken,okx",
                "ACTS_SYMBOL_BLOCKLIST=ADAUSDT,DOTUSDT",
            ]
        )
    )
    monkeypatch.setenv("ACTS_DATABASE_URL", "postgresql+psycopg://from_env/acts")

    settings = AppSettings(_env_file=env_file)

    assert settings.database_url == "postgresql+psycopg://from_env/acts"
    assert settings.output_root == "/tmp/acts-artifacts"
    assert settings.exchanges == ("kraken", "okx")
    assert settings.blocklist_symbols == {"ADAUSDT", "DOTUSDT"}
