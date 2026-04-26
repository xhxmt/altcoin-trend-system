from datetime import datetime, timedelta, timezone

from altcoin_trend.config import AppSettings
from altcoin_trend.ingest import incremental
from altcoin_trend.models import Instrument, MarketBar1m


NOW = datetime(2026, 4, 19, 13, 45, tzinfo=timezone.utc)


def make_instrument(symbol: str) -> Instrument:
    return Instrument(
        exchange="binance",
        market_type="usdt_perp",
        symbol=symbol,
        base_asset=symbol.removesuffix("USDT"),
        quote_asset="USDT",
        status="trading",
        onboard_at=NOW - timedelta(days=365),
        contract_type="PERPETUAL",
        tick_size=0.01,
        step_size=0.1,
        min_notional=5.0,
    )


class FakeAdapter:
    exchange = "binance"

    def __init__(self) -> None:
        self.fetch_calls: list[tuple[str, int, int]] = []

    def fetch_instruments(self):
        return [make_instrument("SOLUSDT"), make_instrument("ETHUSDT")]

    def fetch_klines_1m(self, symbol: str, start_ms: int, end_ms: int):
        self.fetch_calls.append((symbol, start_ms, end_ms))
        return [
            MarketBar1m(
                exchange="binance",
                symbol=symbol,
                ts=NOW - timedelta(minutes=1),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=10.0,
                quote_volume=1000.0,
                trade_count=None,
                taker_buy_base=None,
                taker_buy_quote=None,
                is_closed=True,
            )
        ]


def test_sync_exchange_market_data_fetches_after_latest_market_bar(monkeypatch):
    adapter = FakeAdapter()
    inserted = []

    monkeypatch.setattr(incremental, "upsert_instruments", lambda engine, instruments: {"SOLUSDT": 7})
    monkeypatch.setattr(
        incremental,
        "_latest_market_timestamps",
        lambda engine, asset_ids: {7: NOW - timedelta(minutes=5)},
    )

    def fake_insert_market_rows_ignore_conflicts(engine, rows):
        inserted.extend(rows)
        return len(rows)

    monkeypatch.setattr(incremental, "insert_market_rows_ignore_conflicts", fake_insert_market_rows_ignore_conflicts)

    result = incremental.sync_exchange_market_data(
        adapter=adapter,
        engine=object(),
        settings=AppSettings(symbol_allowlist="SOLUSDT"),
        now=NOW,
    )

    assert result.exchange == "binance"
    assert result.instruments_selected == 1
    assert result.bars_written == 1
    assert adapter.fetch_calls == [
        (
            "SOLUSDT",
            int((NOW - timedelta(minutes=4)).timestamp() * 1000),
            int(NOW.timestamp() * 1000),
        )
    ]
    assert inserted[0]["asset_id"] == 7
    assert inserted[0]["symbol"] == "SOLUSDT"


def test_sync_exchange_market_data_uses_fallback_when_asset_has_no_history(monkeypatch):
    adapter = FakeAdapter()

    monkeypatch.setattr(incremental, "upsert_instruments", lambda engine, instruments: {"SOLUSDT": 7})
    monkeypatch.setattr(incremental, "_latest_market_timestamps", lambda engine, asset_ids: {})
    monkeypatch.setattr(incremental, "insert_market_rows_ignore_conflicts", lambda engine, rows: len(list(rows)))

    incremental.sync_exchange_market_data(
        adapter=adapter,
        engine=object(),
        settings=AppSettings(symbol_allowlist="SOLUSDT"),
        now=NOW,
        fallback_lookback_minutes=30,
    )

    assert adapter.fetch_calls == [
        (
            "SOLUSDT",
            int((NOW - timedelta(minutes=30)).timestamp() * 1000),
            int(NOW.timestamp() * 1000),
        )
    ]


def test_sync_exchange_market_data_continues_after_symbol_fetch_failure(monkeypatch):
    class PartiallyFailingAdapter(FakeAdapter):
        def fetch_klines_1m(self, symbol: str, start_ms: int, end_ms: int):
            self.fetch_calls.append((symbol, start_ms, end_ms))
            if symbol == "SOLUSDT":
                raise RuntimeError("rate limited")
            return super().fetch_klines_1m(symbol, start_ms, end_ms)

    adapter = PartiallyFailingAdapter()
    inserted = []

    monkeypatch.setattr(incremental, "upsert_instruments", lambda engine, instruments: {"SOLUSDT": 7, "ETHUSDT": 8})
    monkeypatch.setattr(incremental, "_latest_market_timestamps", lambda engine, asset_ids: {})

    def fake_insert_market_rows_ignore_conflicts(engine, rows):
        inserted.extend(rows)
        return len(rows)

    monkeypatch.setattr(incremental, "insert_market_rows_ignore_conflicts", fake_insert_market_rows_ignore_conflicts)

    result = incremental.sync_exchange_market_data(
        adapter=adapter,
        engine=object(),
        settings=AppSettings(symbol_allowlist=""),
        now=NOW,
        fallback_lookback_minutes=30,
    )

    assert result.instruments_selected == 2
    assert result.failed_symbols == 1
    assert result.bars_written == 1
    assert [row["symbol"] for row in inserted] == ["ETHUSDT"]


def test_sync_exchange_derivatives_skips_assets_with_recent_derivative_data(monkeypatch):
    adapter = FakeAdapter()

    def fail_fetch(*args, **kwargs):
        raise AssertionError("derivative endpoint should not be called")

    adapter.fetch_open_interest_history = fail_fetch
    monkeypatch.setattr(incremental, "_asset_ids_for_exchange", lambda engine, exchange: {"SOLUSDT": 7})
    monkeypatch.setattr(
        incremental,
        "_latest_derivative_timestamps",
        lambda engine, asset_ids: {7: NOW - timedelta(minutes=10)},
    )

    result = incremental.sync_exchange_derivatives(
        adapter=adapter,
        engine=object(),
        settings=AppSettings(symbol_allowlist="SOLUSDT"),
        now=NOW,
    )

    assert result.exchange == "binance"
    assert result.instruments_selected == 1
    assert result.updates_written == 0
