from altcoin_trend.models import Instrument, MarketBar1m, utc_from_ms


def _filter_value(filters: list[dict], filter_type: str, key: str) -> float | None:
    for item in filters:
        if item.get("filterType") == filter_type and key in item:
            return float(item[key])
    return None


class BinancePublicAdapter:
    exchange = "binance"
    market_type = "usdt_perp"

    def parse_exchange_info(self, payload: dict) -> list[Instrument]:
        instruments: list[Instrument] = []
        for item in payload.get("symbols", []):
            if item.get("quoteAsset") != "USDT" or item.get("contractType") != "PERPETUAL":
                continue
            instruments.append(
                Instrument(
                    exchange=self.exchange,
                    market_type=self.market_type,
                    symbol=item["symbol"],
                    base_asset=item["baseAsset"],
                    quote_asset=item["quoteAsset"],
                    status=item["status"].lower(),
                    onboard_at=utc_from_ms(int(item["onboardDate"])) if item.get("onboardDate") else None,
                    contract_type=item.get("contractType"),
                    tick_size=_filter_value(item.get("filters", []), "PRICE_FILTER", "tickSize"),
                    step_size=_filter_value(item.get("filters", []), "LOT_SIZE", "stepSize"),
                    min_notional=_filter_value(item.get("filters", []), "MIN_NOTIONAL", "notional"),
                )
            )
        return instruments

    def parse_kline_message(self, payload: dict, symbol: str | None = None) -> MarketBar1m | None:
        data = payload.get("data", payload)
        kline = data.get("k", {})
        if not kline:
            return None
        return MarketBar1m(
            exchange=self.exchange,
            symbol=kline["s"],
            ts=utc_from_ms(int(kline["t"])),
            open=float(kline["o"]),
            high=float(kline["h"]),
            low=float(kline["l"]),
            close=float(kline["c"]),
            volume=float(kline["v"]),
            quote_volume=float(kline["q"]),
            trade_count=int(kline["n"]) if kline.get("n") is not None else None,
            taker_buy_base=float(kline["V"]) if kline.get("V") is not None else None,
            taker_buy_quote=float(kline["Q"]) if kline.get("Q") is not None else None,
            is_closed=bool(kline.get("x")),
        )
