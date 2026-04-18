from pathlib import Path

from altcoin_trend.config import AppSettings, load_settings


def test_settings_defaults_point_to_project_paths(monkeypatch):
    monkeypatch.delenv("ACTS_OUTPUT_ROOT", raising=False)

    settings = AppSettings()

    assert settings.default_exchanges == "binance,bybit"
    assert settings.quote_asset == "USDT"
    assert settings.min_quote_volume_24h == 5_000_000
    assert settings.signal_interval_seconds == 60
    assert settings.output_root.endswith("artifacts")
    assert settings.artifacts_dir == Path(settings.output_root)


def test_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("ACTS_DATABASE_URL", "postgresql+psycopg://tester@/acts_test")
    monkeypatch.setenv("ACTS_SYMBOL_ALLOWLIST", "SOLUSDT,ARBUSDT")

    settings = load_settings()

    assert settings.database_url == "postgresql+psycopg://tester@/acts_test"
    assert settings.symbol_allowlist == "SOLUSDT,ARBUSDT"
    assert settings.allowlist_symbols == {"SOLUSDT", "ARBUSDT"}
