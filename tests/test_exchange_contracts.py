import json
from pathlib import Path

from altcoin_trend.exchanges.binance import BinancePublicAdapter
from altcoin_trend.exchanges.bybit import BybitPublicAdapter


FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_binance_exchange_info_parser_returns_usdt_perp_instrument():
    adapter = BinancePublicAdapter()

    instruments = adapter.parse_exchange_info(load_json("binance_exchange_info.json"))

    assert len(instruments) == 1
    instrument = instruments[0]
    assert instrument.exchange == "binance"
    assert instrument.symbol == "SOLUSDT"
    assert instrument.base_asset == "SOL"
    assert instrument.quote_asset == "USDT"
    assert instrument.tick_size == 0.01
    assert instrument.step_size == 0.1
    assert instrument.min_notional == 5.0


def test_binance_kline_ws_parser_returns_closed_bar():
    adapter = BinancePublicAdapter()

    bar = adapter.parse_kline_message(load_json("binance_kline_ws.json"))

    assert bar is not None
    assert bar.exchange == "binance"
    assert bar.symbol == "SOLUSDT"
    assert bar.close == 101.0
    assert bar.quote_volume == 124000.5
    assert bar.trade_count == 222
    assert bar.is_closed is True


def test_bybit_instruments_parser_returns_usdt_perp_instrument():
    adapter = BybitPublicAdapter()

    instruments = adapter.parse_instruments_info(load_json("bybit_instruments_info.json"))

    assert len(instruments) == 1
    instrument = instruments[0]
    assert instrument.exchange == "bybit"
    assert instrument.symbol == "SOLUSDT"
    assert instrument.contract_type == "LinearPerpetual"
    assert instrument.tick_size == 0.01
    assert instrument.step_size == 0.1


def test_bybit_kline_ws_parser_returns_closed_bar():
    adapter = BybitPublicAdapter()

    bar = adapter.parse_kline_message(load_json("bybit_kline_ws.json"), symbol="SOLUSDT")

    assert bar is not None
    assert bar.exchange == "bybit"
    assert bar.symbol == "SOLUSDT"
    assert bar.close == 101.0
    assert bar.quote_volume == 124000.5
    assert bar.is_closed is True
