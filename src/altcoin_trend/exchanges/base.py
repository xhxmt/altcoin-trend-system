from typing import Protocol

from altcoin_trend.models import MarketBar1m


class ExchangeAdapter(Protocol):
    exchange: str

    def parse_kline_message(self, payload: dict, symbol: str | None = None) -> MarketBar1m | None:
        ...

    def list_usdt_perp_symbols(self) -> list[str]:
        ...

    def fetch_klines_1m(self, symbol: str, start_ms: int, end_ms: int) -> list[MarketBar1m]:
        ...
