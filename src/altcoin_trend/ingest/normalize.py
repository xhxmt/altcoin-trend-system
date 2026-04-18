from altcoin_trend.models import MarketBar1m


def market_bar_to_row(asset_id: int, bar: MarketBar1m, data_status: str = "healthy") -> dict:
    return {
        "asset_id": asset_id,
        "exchange": bar.exchange,
        "symbol": bar.symbol,
        "ts": bar.ts,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "quote_volume": bar.quote_volume,
        "trade_count": bar.trade_count,
        "taker_buy_base": bar.taker_buy_base,
        "taker_buy_quote": bar.taker_buy_quote,
        "data_status": data_status,
        "reason_codes": [],
    }
