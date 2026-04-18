from datetime import datetime, timedelta, timezone

from altcoin_trend.config import AppSettings
from altcoin_trend.exchanges.binance import BinancePublicAdapter
from altcoin_trend.exchanges.bybit import BybitPublicAdapter
from altcoin_trend.ingest.bootstrap import filter_instruments
from altcoin_trend.ingest.normalize import market_bar_to_row
from altcoin_trend.models import Instrument, MarketBar1m


NOW = datetime(2026, 4, 18, tzinfo=timezone.utc)


def make_instrument(
    symbol: str,
    *,
    market_type: str = "usdt_perp",
    quote_asset: str = "USDT",
    status: str = "trading",
    onboard_at: datetime | None = None,
) -> Instrument:
    return Instrument(
        exchange="binance",
        market_type=market_type,
        symbol=symbol,
        base_asset=symbol.removesuffix("USDT"),
        quote_asset=quote_asset,
        status=status,
        onboard_at=onboard_at,
        contract_type="PERPETUAL",
        tick_size=0.01,
        step_size=0.1,
        min_notional=5.0,
    )


def test_filter_instruments_keeps_only_trading_usdt_perp_and_respects_blocklist():
    settings = AppSettings(
        symbol_blocklist="BANNED",
    )
    instruments = [
        make_instrument("GOOD", onboard_at=NOW - timedelta(days=365)),
        make_instrument("WRONGQUOTE", quote_asset="BTC", onboard_at=NOW - timedelta(days=365)),
        make_instrument("WRONGTYPE", market_type="spot", onboard_at=NOW - timedelta(days=365)),
        make_instrument("HALTED", status="halted", onboard_at=NOW - timedelta(days=365)),
        make_instrument("BANNED", onboard_at=NOW - timedelta(days=365)),
    ]

    filtered = filter_instruments(instruments, settings, NOW)

    assert [instrument.symbol for instrument in filtered] == ["GOOD"]


def test_filter_instruments_honors_allowlist_and_preserves_order():
    settings = AppSettings(
        symbol_allowlist="SOLUSDT,ETHUSDT",
    )
    instruments = [
        make_instrument("ETHUSDT", onboard_at=NOW - timedelta(days=365)),
        make_instrument("SOLUSDT", onboard_at=NOW - timedelta(days=365)),
        make_instrument("XRPUSDT", onboard_at=NOW - timedelta(days=365)),
    ]

    filtered = filter_instruments(instruments, settings, NOW)

    assert [instrument.symbol for instrument in filtered] == ["ETHUSDT", "SOLUSDT"]


def test_filter_instruments_enforces_min_listing_age_boundary():
    settings = AppSettings(min_listing_days=60)
    instruments = [
        make_instrument("OLD", onboard_at=NOW - timedelta(days=60)),
        make_instrument("TOO_NEW", onboard_at=NOW - timedelta(days=59)),
        make_instrument("NO_LISTING_DATE", onboard_at=None),
    ]

    filtered = filter_instruments(instruments, settings, NOW)

    assert [instrument.symbol for instrument in filtered] == ["OLD", "NO_LISTING_DATE"]


def test_market_bar_to_row_normalizes_expected_fields():
    bar = MarketBar1m(
        exchange="binance",
        symbol="SOLUSDT",
        ts=NOW,
        open=100.0,
        high=102.0,
        low=99.5,
        close=101.0,
        volume=1234.5,
        quote_volume=124000.5,
        trade_count=222,
        taker_buy_base=600.0,
        taker_buy_quote=60600.0,
        is_closed=True,
    )

    row = market_bar_to_row(17, bar, data_status="healthy")

    assert row == {
        "asset_id": 17,
        "exchange": "binance",
        "symbol": "SOLUSDT",
        "ts": NOW,
        "open": 100.0,
        "high": 102.0,
        "low": 99.5,
        "close": 101.0,
        "volume": 1234.5,
        "quote_volume": 124000.5,
        "trade_count": 222,
        "taker_buy_base": 600.0,
        "taker_buy_quote": 60600.0,
        "data_status": "healthy",
        "reason_codes": [],
    }


def test_binance_parse_rest_klines_converts_row():
    adapter = BinancePublicAdapter()
    rows = [
        [
            1710000000000,
            "100.0",
            "102.0",
            "99.5",
            "101.0",
            "1234.5",
            1710000059999,
            "124000.5",
            222,
            "600.0",
            "60600.0",
            "0",
        ]
    ]

    bars = adapter.parse_rest_klines("SOLUSDT", rows)

    assert len(bars) == 1
    bar = bars[0]
    assert bar.exchange == "binance"
    assert bar.symbol == "SOLUSDT"
    assert bar.close == 101.0
    assert bar.is_closed is True


def test_bybit_parse_rest_klines_converts_row():
    adapter = BybitPublicAdapter()
    rows = [
        [
            "1710000000000",
            "100.0",
            "102.0",
            "99.5",
            "101.0",
            "1234.5",
            "124000.5",
        ]
    ]

    bars = adapter.parse_rest_klines("SOLUSDT", rows)

    assert len(bars) == 1
    bar = bars[0]
    assert bar.exchange == "bybit"
    assert bar.symbol == "SOLUSDT"
    assert bar.quote_volume == 124000.5
    assert bar.is_closed is True
