from altcoin_trend.models import Instrument, MarketBar1m, utc_from_ms


class BybitPublicAdapter:
    exchange = "bybit"
    market_type = "usdt_perp"

    def list_usdt_perp_symbols(self) -> list[str]:
        raise NotImplementedError("BybitPublicAdapter does not implement live symbol listing")

    def fetch_klines_1m(self, symbol: str, start_ms: int, end_ms: int) -> list[MarketBar1m]:
        raise NotImplementedError("BybitPublicAdapter does not implement live kline fetching")

    def parse_instruments_info(self, payload: dict) -> list[Instrument]:
        instruments: list[Instrument] = []
        for item in payload.get("result", {}).get("list", []):
            if item.get("quoteCoin") != "USDT" or item.get("contractType") != "LinearPerpetual":
                continue
            price_filter = item.get("priceFilter", {})
            lot_filter = item.get("lotSizeFilter", {})
            instruments.append(
                Instrument(
                    exchange=self.exchange,
                    market_type=self.market_type,
                    symbol=item["symbol"],
                    base_asset=item["baseCoin"],
                    quote_asset=item["quoteCoin"],
                    status=item["status"].lower(),
                    onboard_at=utc_from_ms(int(item["launchTime"])) if item.get("launchTime") else None,
                    contract_type=item.get("contractType"),
                    tick_size=float(price_filter["tickSize"]) if price_filter.get("tickSize") else None,
                    step_size=float(lot_filter["qtyStep"]) if lot_filter.get("qtyStep") else None,
                    min_notional=float(lot_filter["minNotionalValue"]) if lot_filter.get("minNotionalValue") else None,
                )
            )
        return instruments

    def parse_kline_message(self, payload: dict, symbol: str | None = None) -> MarketBar1m | None:
        rows = payload.get("data") or []
        if not rows:
            return None
        row = rows[0]
        topic = payload.get("topic")
        topic_symbol = symbol
        if topic_symbol is None and isinstance(topic, str):
            parts = topic.split(".")
            if parts and parts[-1]:
                topic_symbol = parts[-1]
        if topic_symbol is None:
            return None
        return MarketBar1m(
            exchange=self.exchange,
            symbol=topic_symbol,
            ts=utc_from_ms(int(row["start"])),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            quote_volume=float(row["turnover"]),
            trade_count=None,
            taker_buy_base=None,
            taker_buy_quote=None,
            is_closed=bool(row.get("confirm")),
        )
