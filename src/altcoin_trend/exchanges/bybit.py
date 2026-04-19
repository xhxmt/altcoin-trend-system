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


class BybitPublicAdapter:
    exchange = "bybit"
    market_type = "usdt_perp"
    base_url = "https://api.bybit.com"

    def list_usdt_perp_symbols(self) -> list[str]:
        return [instrument.symbol for instrument in self.fetch_instruments()]

    def fetch_instruments(self) -> list[Instrument]:
        instruments: list[Instrument] = []
        cursor: str | None = None
        while True:
            params = {"category": "linear", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            response = httpx.get(f"{self.base_url}/v5/market/instruments-info", params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Malformed Bybit instruments response: payload must be a mapping")
            ret_code = payload.get("retCode")
            ret_msg = payload.get("retMsg")
            if ret_code != 0:
                raise ValueError(f"Bybit instruments request failed: retCode={ret_code} retMsg={ret_msg}")
            result = payload.get("result")
            if not isinstance(result, dict):
                raise ValueError("Malformed Bybit instruments response: missing result mapping")
            instruments.extend(self.parse_instruments_info(payload))
            next_cursor = result.get("nextPageCursor")
            if not isinstance(next_cursor, str) or not next_cursor.strip():
                break
            cursor = next_cursor
        return instruments

    def fetch_klines_1m(self, symbol: str, start_ms: int, end_ms: int) -> list[MarketBar1m]:
        bars: list[MarketBar1m] = []
        next_start = start_ms
        while next_start <= end_ms:
            response = httpx.get(
                f"{self.base_url}/v5/market/kline",
                params={
                    "category": "linear",
                    "symbol": symbol,
                    "interval": "1",
                    "start": next_start,
                    "end": end_ms,
                    "limit": 1000,
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Malformed Bybit klines response: payload must be a mapping")

            ret_code = payload.get("retCode")
            ret_msg = payload.get("retMsg")
            if ret_code != 0:
                raise ValueError(f"Bybit kline request failed: retCode={ret_code} retMsg={ret_msg}")

            result = payload.get("result")
            if not isinstance(result, dict):
                raise ValueError("Malformed Bybit klines response: missing result mapping")
            rows = result.get("list", [])
            if not isinstance(rows, list):
                raise ValueError("Malformed Bybit klines response: result.list must be a list")

            page = sorted(self.parse_rest_klines(symbol, rows), key=lambda bar: bar.ts)
            if not page:
                break
            bars.extend(page)
            last_ms = int(page[-1].ts.timestamp() * 1000)
            advanced_start = last_ms + 60_000
            if advanced_start <= next_start:
                break
            next_start = advanced_start
            if next_start >= end_ms:
                break
        return sorted(bars, key=lambda bar: bar.ts)

    def parse_rest_klines(self, symbol: str, rows: list[list[str]]) -> list[MarketBar1m]:
        bars: list[MarketBar1m] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 7:
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
                        quote_volume=_finite_float(row[6]),
                        trade_count=None,
                        taker_buy_base=None,
                        taker_buy_quote=None,
                        is_closed=True,
                    )
                )
            except (TypeError, ValueError, KeyError):
                continue
        return bars

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
        if not isinstance(topic, str):
            return None
        parts = topic.split(".")
        if len(parts) != 3 or parts[0] != "kline" or parts[1] != "1" or not parts[2].strip():
            return None
        topic_symbol = parts[2]
        if symbol is not None and _nonempty_str(symbol) != topic_symbol:
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
