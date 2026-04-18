from dataclasses import dataclass
from datetime import datetime, timezone


def utc_from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


@dataclass(frozen=True)
class Instrument:
    exchange: str
    market_type: str
    symbol: str
    base_asset: str
    quote_asset: str
    status: str
    onboard_at: datetime | None
    contract_type: str | None
    tick_size: float | None
    step_size: float | None
    min_notional: float | None


@dataclass(frozen=True)
class MarketBar1m:
    exchange: str
    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trade_count: int | None
    taker_buy_base: float | None
    taker_buy_quote: float | None
    is_closed: bool
