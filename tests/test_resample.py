import pandas as pd

from altcoin_trend.features.resample import resample_market_1m


def test_resample_market_1m_aggregates_one_5m_bucket():
    frame = pd.DataFrame(
        [
            {
                "ts": "2024-03-01T00:00:00Z",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 10.0,
                "quote_volume": 1000.0,
                "trade_count": 1,
            },
            {
                "ts": "2024-03-01T00:01:00Z",
                "open": 100.5,
                "high": 102.0,
                "low": 100.0,
                "close": 101.0,
                "volume": 20.0,
                "quote_volume": 2000.0,
                "trade_count": 2,
            },
            {
                "ts": "2024-03-01T00:02:00Z",
                "open": 101.0,
                "high": 103.0,
                "low": 100.5,
                "close": 102.0,
                "volume": 30.0,
                "quote_volume": 3000.0,
                "trade_count": 3,
            },
            {
                "ts": "2024-03-01T00:03:00Z",
                "open": 102.0,
                "high": 104.0,
                "low": 101.0,
                "close": 103.0,
                "volume": 40.0,
                "quote_volume": 4000.0,
                "trade_count": 4,
            },
            {
                "ts": "2024-03-01T00:04:00Z",
                "open": 103.0,
                "high": 105.0,
                "low": 102.0,
                "close": 104.5,
                "volume": 50.0,
                "quote_volume": 5000.0,
                "trade_count": 5,
            },
        ]
    )

    result = resample_market_1m(frame, "5m")

    assert len(result) == 1
    row = result.iloc[0]
    assert row["open"] == 100.0
    assert row["high"] == 105.0
    assert row["low"] == 99.0
    assert row["close"] == 104.5
    assert row["volume"] == 150.0
    assert row["quote_volume"] == 15000.0
    assert row["trade_count"] == 15


def test_resample_market_1m_returns_copy_for_empty_frame():
    frame = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume", "quote_volume", "trade_count"])

    result = resample_market_1m(frame, "5m")

    assert result.empty
    assert result is not frame


def test_resample_market_1m_rejects_unsupported_timeframe():
    frame = pd.DataFrame(
        [
            {
                "ts": "2024-03-01T00:00:00Z",
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
                "quote_volume": 1.0,
                "trade_count": 1,
            }
        ]
    )

    try:
        resample_market_1m(frame, "2m")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unsupported timeframe")


def test_resample_market_1m_includes_optional_columns():
    frame = pd.DataFrame(
        [
            {
                "ts": "2024-03-01T00:00:00Z",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 10.0,
                "quote_volume": 1000.0,
                "trade_count": 1,
                "taker_buy_base": 4.0,
                "taker_buy_quote": 400.0,
                "open_interest": 50.0,
                "funding_rate": 0.01,
                "long_short_ratio": 1.2,
                "buy_sell_ratio": 0.8,
            },
            {
                "ts": "2024-03-01T00:01:00Z",
                "open": 100.5,
                "high": 102.0,
                "low": 100.0,
                "close": 101.0,
                "volume": 20.0,
                "quote_volume": 2000.0,
                "trade_count": 2,
                "taker_buy_base": 6.0,
                "taker_buy_quote": 600.0,
                "open_interest": 55.0,
                "funding_rate": 0.02,
                "long_short_ratio": 1.4,
                "buy_sell_ratio": 0.9,
            },
        ]
    )

    result = resample_market_1m(frame, "5m")

    row = result.iloc[0]
    assert row["taker_buy_base"] == 10.0
    assert row["taker_buy_quote"] == 1000.0
    assert row["open_interest"] == 55.0
    assert row["funding_rate"] == 0.02
    assert row["long_short_ratio"] == 1.4
    assert row["buy_sell_ratio"] == 0.9


def test_resample_market_1m_sorts_by_timestamp_before_resampling():
    frame = pd.DataFrame(
        [
            {
                "ts": "2024-03-01T00:01:00Z",
                "open": 101.0,
                "high": 101.5,
                "low": 100.5,
                "close": 101.25,
                "volume": 20.0,
                "quote_volume": 2000.0,
                "trade_count": 2,
            },
            {
                "ts": "2024-03-01T00:00:00Z",
                "open": 100.0,
                "high": 100.5,
                "low": 99.5,
                "close": 100.25,
                "volume": 10.0,
                "quote_volume": 1000.0,
                "trade_count": 1,
            },
        ]
    )

    result = resample_market_1m(frame, "5m")

    assert result.iloc[0]["open"] == 100.0
    assert result.iloc[0]["close"] == 101.25


def test_resample_market_1m_drops_bucket_when_positional_ohlc_is_missing():
    frame = pd.DataFrame(
        [
            {
                "ts": "2024-03-01T00:00:00Z",
                "open": None,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 10.0,
                "quote_volume": 1000.0,
                "trade_count": 1,
            },
            {
                "ts": "2024-03-01T00:01:00Z",
                "open": 100.5,
                "high": 102.0,
                "low": 100.0,
                "close": None,
                "volume": 20.0,
                "quote_volume": 2000.0,
                "trade_count": 2,
            },
        ]
    )

    result = resample_market_1m(frame, "5m")

    assert result.empty
