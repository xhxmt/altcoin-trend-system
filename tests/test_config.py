from pathlib import Path
import importlib

import pytest

import altcoin_trend.config as config_module


def test_settings_defaults_point_to_project_paths(monkeypatch):
    for key in (
        "ACTS_DATABASE_URL",
        "ACTS_OUTPUT_ROOT",
        "ACTS_DEFAULT_EXCHANGES",
        "ACTS_QUOTE_ASSET",
        "ACTS_MIN_QUOTE_VOLUME_24H",
        "ACTS_MIN_LISTING_DAYS",
        "ACTS_BOOTSTRAP_LOOKBACK_DAYS",
        "ACTS_SNAPSHOT_LOOKBACK_DAYS",
        "ACTS_STALE_MARKET_SECONDS",
        "ACTS_SIGNAL_INTERVAL_SECONDS",
        "ACTS_DAEMON_FAILURE_BACKOFF_MAX_SECONDS",
        "ACTS_ALERT_COOLDOWN_SECONDS",
        "ACTS_TELEGRAM_BOT_TOKEN",
        "ACTS_TELEGRAM_CHAT_ID",
        "ACTS_SYMBOL_ALLOWLIST",
        "ACTS_SYMBOL_BLOCKLIST",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = config_module.AppSettings()

    assert settings.default_exchanges == "binance,bybit"
    assert settings.exchanges == ("binance", "bybit")
    assert settings.quote_asset == "USDT"
    assert settings.min_quote_volume_24h == 5_000_000
    assert settings.snapshot_lookback_days == 31
    assert settings.stale_market_seconds == 3600
    assert settings.signal_interval_seconds == 60
    assert settings.daemon_failure_backoff_max_seconds == 300
    assert settings.output_root == str(Path(__file__).resolve().parents[1] / "artifacts")
    assert settings.artifacts_dir == Path(settings.output_root)
    assert settings.blocklist_symbols == set()


def test_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("ACTS_DATABASE_URL", "postgresql+psycopg://tester@/acts_test")
    monkeypatch.setenv("ACTS_SYMBOL_ALLOWLIST", "SOLUSDT,ARBUSDT")
    monkeypatch.setenv("ACTS_SYMBOL_BLOCKLIST", "  btcusdt , ethusdt ")

    settings = config_module.load_settings()

    assert settings.database_url == "postgresql+psycopg://tester@/acts_test"
    assert settings.symbol_allowlist == "SOLUSDT,ARBUSDT"
    assert settings.allowlist_symbols == {"SOLUSDT", "ARBUSDT"}
    assert settings.blocklist_symbols == {"BTCUSDT", "ETHUSDT"}


def test_settings_runtime_validation_rejects_unsupported_or_conflicting_values():
    unsupported = config_module.AppSettings(default_exchanges="binance,kraken")
    with pytest.raises(ValueError, match="Unsupported ACTS_DEFAULT_EXCHANGES"):
        unsupported.validate_runtime()

    non_positive = config_module.AppSettings(stale_market_seconds=0)
    with pytest.raises(ValueError, match="ACTS_STALE_MARKET_SECONDS"):
        non_positive.validate_runtime()

    overlap = config_module.AppSettings(symbol_allowlist="SOLUSDT,ETHUSDT", symbol_blocklist="ethusdt")
    with pytest.raises(ValueError, match="Symbols cannot appear in both"):
        overlap.validate_runtime()


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

    settings = config_module.AppSettings(_env_file=env_file)

    assert settings.database_url == "postgresql+psycopg://from_env/acts"
    assert settings.output_root == "/tmp/acts-artifacts"
    assert settings.exchanges == ("kraken", "okx")
    assert settings.blocklist_symbols == {"ADAUSDT", "DOTUSDT"}


def test_settings_default_env_file_lookup_uses_home_dot_config(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    env_dir = home_dir / ".config" / "acts"
    env_dir.mkdir(parents=True)
    (env_dir / "acts.env").write_text(
        "\n".join(
            [
                "ACTS_DATABASE_URL=postgresql+psycopg://from_default_file/acts",
                "ACTS_DEFAULT_EXCHANGES=kucoin,gate",
                "ACTS_SYMBOL_BLOCKLIST=APTUSDT,NEARUSDT",
            ]
        )
    )

    for key in (
        "ACTS_DATABASE_URL",
        "ACTS_OUTPUT_ROOT",
        "ACTS_DEFAULT_EXCHANGES",
        "ACTS_SYMBOL_ALLOWLIST",
        "ACTS_SYMBOL_BLOCKLIST",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(home_dir))

    reloaded = importlib.reload(config_module)
    settings = reloaded.AppSettings()

    assert settings.database_url == "postgresql+psycopg://from_default_file/acts"
    assert settings.exchanges == ("kucoin", "gate")
    assert settings.blocklist_symbols == {"APTUSDT", "NEARUSDT"}
