from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


_DEFAULT_ENV_FILE = Path.home() / ".config" / "acts" / "acts.env"


class AppSettings(BaseSettings):
    database_url: str = "postgresql+psycopg://tfisher@/altcoin_trend"
    output_root: str = "/home/tfisher/altcoin-trend-system/artifacts"
    default_exchanges: str = "binance,bybit"
    quote_asset: str = "USDT"
    min_quote_volume_24h: float = 5_000_000
    min_listing_days: int = 60
    bootstrap_lookback_days: int = 90
    signal_interval_seconds: int = 60
    alert_cooldown_seconds: int = 14_400
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    symbol_allowlist: str = ""
    symbol_blocklist: str = ""

    model_config = SettingsConfigDict(
        env_prefix="ACTS_",
        env_file=_DEFAULT_ENV_FILE,
        extra="ignore",
    )

    @property
    def artifacts_dir(self) -> Path:
        return Path(self.output_root)

    @property
    def exchanges(self) -> tuple[str, ...]:
        return tuple(item.strip() for item in self.default_exchanges.split(",") if item.strip())

    @property
    def allowlist_symbols(self) -> set[str]:
        return {item.strip().upper() for item in self.symbol_allowlist.split(",") if item.strip()}

    @property
    def blocklist_symbols(self) -> set[str]:
        return {item.strip().upper() for item in self.symbol_blocklist.split(",") if item.strip()}


def load_settings() -> AppSettings:
    return AppSettings()
