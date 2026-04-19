from datetime import datetime, timedelta, timezone

from altcoin_trend.config import AppSettings
from altcoin_trend.ingest import bootstrap as bootstrap_module
from altcoin_trend.ingest.bootstrap import bootstrap_exchange
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


class _FakeBootstrapAdapter:
    exchange = "binance"

    def __init__(self):
        self.fetch_calls = []

    def fetch_instruments(self):
        return [
            make_instrument("SOLUSDT", onboard_at=NOW - timedelta(days=365)),
            make_instrument("NEWUSDT", onboard_at=NOW - timedelta(days=1)),
        ]

    def fetch_klines_1m(self, symbol: str, start_ms: int, end_ms: int):
        self.fetch_calls.append((symbol, start_ms, end_ms))
        return [
            MarketBar1m(
                exchange="binance",
                symbol=symbol,
                ts=NOW - timedelta(minutes=1),
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
        ]


def test_bootstrap_exchange_filters_fetches_and_writes_market_rows(monkeypatch):
    adapter = _FakeBootstrapAdapter()
    inserted = []

    monkeypatch.setattr(
        bootstrap_module,
        "upsert_instruments",
        lambda engine, instruments: {instrument.symbol: index + 10 for index, instrument in enumerate(instruments)},
    )

    def fake_insert_market_rows_ignore_conflicts(engine, rows):
        inserted.append(("alt_core.market_1m", list(rows)))
        return len(inserted[-1][1])

    monkeypatch.setattr(
        bootstrap_module,
        "insert_market_rows_ignore_conflicts",
        fake_insert_market_rows_ignore_conflicts,
    )

    result = bootstrap_exchange(
        adapter=adapter,
        engine=object(),
        settings=AppSettings(min_listing_days=60),
        lookback_days=2,
        now=NOW,
    )

    assert result.exchange == "binance"
    assert result.instruments_selected == 1
    assert result.bars_written == 1
    assert adapter.fetch_calls == [
        (
            "SOLUSDT",
            int((NOW - timedelta(days=2)).timestamp() * 1000),
            int(NOW.timestamp() * 1000),
        )
    ]
    assert inserted[0][0] == "alt_core.market_1m"
    assert inserted[0][1][0]["asset_id"] == 10
    assert inserted[0][1][0]["symbol"] == "SOLUSDT"


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
