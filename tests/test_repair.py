from datetime import datetime, timezone

from altcoin_trend.exchanges.ws import StreamSubscription, binance_kline_stream_name, bybit_kline_topic
from altcoin_trend.ingest.live import accept_closed_bar
from altcoin_trend.ingest.repair import compute_missing_1m_ranges
from altcoin_trend.models import MarketBar1m


def dt(minute: int) -> datetime:
    return datetime(2026, 4, 18, 0, minute, tzinfo=timezone.utc)


def make_bar(*, is_closed: bool) -> MarketBar1m:
    return MarketBar1m(
        exchange="binance",
        symbol="SOLUSDT",
        ts=dt(0),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=123.0,
        quote_volume=456.0,
        trade_count=7,
        taker_buy_base=None,
        taker_buy_quote=None,
        is_closed=is_closed,
    )


def test_compute_missing_1m_ranges_returns_empty_when_incoming_is_next_minute():
    assert compute_missing_1m_ranges(last_closed_ts=dt(0), incoming_ts=dt(1)) == []


def test_compute_missing_1m_ranges_returns_single_inclusive_gap():
    assert compute_missing_1m_ranges(last_closed_ts=dt(0), incoming_ts=dt(4)) == [(dt(1), dt(3))]


def test_compute_missing_1m_ranges_returns_empty_without_last_closed_ts():
    assert compute_missing_1m_ranges(last_closed_ts=None, incoming_ts=dt(4)) == []


def test_accept_closed_bar_returns_bar_closed_state():
    assert accept_closed_bar(make_bar(is_closed=True)) is True
    assert accept_closed_bar(make_bar(is_closed=False)) is False


def test_stream_subscription_and_topic_helpers_format_expected_values():
    subscription = StreamSubscription(exchange="binance", stream_name=binance_kline_stream_name("SOLUSDT"))

    assert subscription == StreamSubscription(exchange="binance", stream_name="solusdt@kline_1m")
    assert subscription.symbol is None
    assert binance_kline_stream_name("SOLUSDT") == "solusdt@kline_1m"
    assert bybit_kline_topic("solusdt") == "kline.1.SOLUSDT"
