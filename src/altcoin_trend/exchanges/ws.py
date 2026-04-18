from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StreamSubscription:
    exchange: str
    stream_name: str
    symbol: str | None = None


def binance_kline_stream_name(symbol: str) -> str:
    return f"{symbol.lower()}@kline_1m"


def bybit_kline_topic(symbol: str) -> str:
    return f"kline.1.{symbol.upper()}"
