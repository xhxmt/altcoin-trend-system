import math

from altcoin_trend.models import Instrument, MarketBar1m, utc_from_ms


def _nonempty_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _finite_float(value: object) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("value must be finite")
    return number


class BybitPublicAdapter:
    exchange = "bybit"
    market_type = "usdt_perp"

    def list_usdt_perp_symbols(self) -> list[str]:
        raise NotImplementedError("BybitPublicAdapter does not implement live symbol listing")

    def fetch_klines_1m(self, symbol: str, start_ms: int, end_ms: int) -> list[MarketBar1m]:
        raise NotImplementedError("BybitPublicAdapter does not implement live kline fetching")

    def parse_instruments_info(self, payload: dict) -> list[Instrument]:
        if not isinstance(payload, dict):
            return []
        result = payload.get("result")
        if not isinstance(result, dict):
            return []
        items = result.get("list", [])
        if not isinstance(items, list):
            return []
        instruments: list[Instrument] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            required_fields = ("symbol", "baseCoin", "quoteCoin", "status", "contractType")
            if any(_nonempty_str(item.get(field)) is None for field in required_fields):
                continue
            if item["quoteCoin"] != "USDT" or item["contractType"] != "LinearPerpetual":
                continue
            price_filter = item.get("priceFilter", {})
            lot_filter = item.get("lotSizeFilter", {})
            if not isinstance(price_filter, dict) or not isinstance(lot_filter, dict):
                continue
            try:
                instruments.append(
                    Instrument(
                        exchange=self.exchange,
                        market_type=self.market_type,
                        symbol=_nonempty_str(item["symbol"]) or "",
                        base_asset=_nonempty_str(item["baseCoin"]) or "",
                        quote_asset=_nonempty_str(item["quoteCoin"]) or "",
                        status=item["status"].lower(),
                        onboard_at=utc_from_ms(int(item["launchTime"])) if item.get("launchTime") else None,
                        contract_type=item.get("contractType"),
                        tick_size=_finite_float(price_filter["tickSize"]) if price_filter.get("tickSize") else None,
                        step_size=_finite_float(lot_filter["qtyStep"]) if lot_filter.get("qtyStep") else None,
                        min_notional=_finite_float(lot_filter["minNotionalValue"]) if lot_filter.get("minNotionalValue") else None,
                    )
                )
            except (TypeError, ValueError, KeyError):
                continue
        return instruments

    def parse_kline_message(self, payload: dict, symbol: str | None = None) -> MarketBar1m | None:
        if not isinstance(payload, dict):
            return None
        rows = payload.get("data") or []
        if not isinstance(rows, list) or not rows:
            return None
        row = rows[0]
        topic = payload.get("topic")
        topic_symbol = symbol
        if topic_symbol is not None:
            topic_symbol = _nonempty_str(topic_symbol)
        elif isinstance(topic, str):
            parts = topic.split(".")
            if len(parts) == 3 and parts[0] == "kline" and parts[1].strip() and parts[2].strip():
                topic_symbol = parts[2]
        if topic_symbol is None:
            return None
        required_fields = ("start", "open", "high", "low", "close", "volume", "turnover", "confirm")
        if not isinstance(row, dict) or any(field not in row for field in required_fields):
            return None
        if not isinstance(row.get("confirm"), bool):
            return None
        if _nonempty_str(topic_symbol) is None:
            return None
        try:
            return MarketBar1m(
                exchange=self.exchange,
                symbol=topic_symbol,
                ts=utc_from_ms(int(row["start"])),
                open=_finite_float(row["open"]),
                high=_finite_float(row["high"]),
                low=_finite_float(row["low"]),
                close=_finite_float(row["close"]),
                volume=_finite_float(row["volume"]),
                quote_volume=_finite_float(row["turnover"]),
                trade_count=None,
                taker_buy_base=None,
                taker_buy_quote=None,
                is_closed=row["confirm"],
            )
        except (TypeError, ValueError, KeyError):
            return None
