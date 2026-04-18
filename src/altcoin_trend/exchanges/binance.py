import math

import httpx

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


def _filter_value(filters: list[dict], filter_type: str, key: str) -> float | None:
    for item in filters:
        if not isinstance(item, dict):
            continue
        if item.get("filterType") == filter_type and key in item:
            return _finite_float(item[key])
    return None


class BinancePublicAdapter:
    exchange = "binance"
    market_type = "usdt_perp"
    base_url = "https://fapi.binance.com"

    def list_usdt_perp_symbols(self) -> list[str]:
        return [instrument.symbol for instrument in self.fetch_instruments()]

    def fetch_instruments(self) -> list[Instrument]:
        response = httpx.get(f"{self.base_url}/fapi/v1/exchangeInfo", timeout=20)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Malformed Binance exchange info response: payload must be a mapping")
        return self.parse_exchange_info(payload)

    def fetch_klines_1m(self, symbol: str, start_ms: int, end_ms: int) -> list[MarketBar1m]:
        response = httpx.get(
            f"{self.base_url}/fapi/v1/klines",
            params={
                "symbol": symbol,
                "interval": "1m",
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 1500,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Malformed Binance klines response: payload must be a list")
        return self.parse_rest_klines(symbol, payload)

    def parse_rest_klines(self, symbol: str, rows: list[list]) -> list[MarketBar1m]:
        bars: list[MarketBar1m] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 11:
                continue
            try:
                bars.append(
                    MarketBar1m(
                        exchange=self.exchange,
                        symbol=symbol,
                        ts=utc_from_ms(int(row[0])),
                        open=_finite_float(row[1]),
                        high=_finite_float(row[2]),
                        low=_finite_float(row[3]),
                        close=_finite_float(row[4]),
                        volume=_finite_float(row[5]),
                        quote_volume=_finite_float(row[7]),
                        trade_count=int(row[8]) if row[8] is not None else None,
                        taker_buy_base=_finite_float(row[9]) if row[9] is not None else None,
                        taker_buy_quote=_finite_float(row[10]) if row[10] is not None else None,
                        is_closed=True,
                    )
                )
            except (TypeError, ValueError, KeyError):
                continue
        return bars

    def parse_exchange_info(self, payload: dict) -> list[Instrument]:
        if not isinstance(payload, dict):
            return []
        symbols = payload.get("symbols", [])
        if not isinstance(symbols, list):
            return []
        instruments: list[Instrument] = []
        for item in symbols:
            if not isinstance(item, dict):
                continue
            required_fields = ("symbol", "baseAsset", "quoteAsset", "status", "contractType")
            if any(_nonempty_str(item.get(field)) is None for field in required_fields):
                continue
            if item["quoteAsset"] != "USDT" or item["contractType"] != "PERPETUAL":
                continue
            try:
                instruments.append(
                    Instrument(
                        exchange=self.exchange,
                        market_type=self.market_type,
                        symbol=_nonempty_str(item["symbol"]) or "",
                        base_asset=_nonempty_str(item["baseAsset"]) or "",
                        quote_asset=_nonempty_str(item["quoteAsset"]) or "",
                        status=item["status"].lower(),
                        onboard_at=utc_from_ms(int(item["onboardDate"])) if item.get("onboardDate") else None,
                        contract_type=item.get("contractType"),
                        tick_size=_filter_value(item.get("filters", []), "PRICE_FILTER", "tickSize"),
                        step_size=_filter_value(item.get("filters", []), "LOT_SIZE", "stepSize"),
                        min_notional=_filter_value(item.get("filters", []), "MIN_NOTIONAL", "notional"),
                    )
                )
            except (TypeError, ValueError, KeyError):
                continue
        return instruments

    def parse_kline_message(self, payload: dict, symbol: str | None = None) -> MarketBar1m | None:
        if not isinstance(payload, dict):
            return None
        if symbol is not None and _nonempty_str(symbol) is None:
            return None
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            return None
        kline = data.get("k")
        if not isinstance(kline, dict) or not kline:
            return None
        required_fields = ("s", "i", "t", "o", "h", "l", "c", "v", "q", "x")
        if any(field not in kline for field in required_fields):
            return None
        parsed_symbol = _nonempty_str(kline.get("s"))
        if parsed_symbol is None or kline.get("i") != "1m" or not isinstance(kline.get("x"), bool):
            return None
        if symbol is not None and symbol.upper() != parsed_symbol.upper():
            return None
        try:
            return MarketBar1m(
                exchange=self.exchange,
                symbol=parsed_symbol,
                ts=utc_from_ms(int(kline["t"])),
                open=_finite_float(kline["o"]),
                high=_finite_float(kline["h"]),
                low=_finite_float(kline["l"]),
                close=_finite_float(kline["c"]),
                volume=_finite_float(kline["v"]),
                quote_volume=_finite_float(kline["q"]),
                trade_count=int(kline["n"]) if kline.get("n") is not None else None,
                taker_buy_base=_finite_float(kline["V"]) if kline.get("V") is not None else None,
                taker_buy_quote=_finite_float(kline["Q"]) if kline.get("Q") is not None else None,
                is_closed=kline["x"],
            )
        except (TypeError, ValueError, KeyError):
            return None
