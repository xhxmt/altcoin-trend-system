from __future__ import annotations

import pandas as pd


_TIMEFRAME_RULES = {
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1D",
}

_OHLC_COLUMNS = ("open", "high", "low", "close")
_BASE_AGGREGATIONS = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
    "quote_volume": "sum",
    "trade_count": "sum",
}


def resample_market_1m(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe not in _TIMEFRAME_RULES:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    if frame.empty:
        return frame.copy()
    if "ts" not in frame.columns:
        raise KeyError("ts")

    indexed = frame.copy()
    indexed["ts"] = pd.to_datetime(indexed["ts"], utc=True)
    indexed = indexed.sort_values("ts").set_index("ts")

    aggregations: dict[str, str] = dict(_BASE_AGGREGATIONS)
    for column in ("taker_buy_base", "taker_buy_quote"):
        if column in indexed.columns:
            aggregations[column] = "sum"
    for column in ("open_interest", "funding_rate", "long_short_ratio", "buy_sell_ratio"):
        if column in indexed.columns:
            aggregations[column] = "last"

    resampled = indexed.resample(_TIMEFRAME_RULES[timeframe], label="left", closed="left").agg(aggregations)
    resampled = resampled.dropna(subset=list(_OHLC_COLUMNS))
    return resampled.reset_index()
