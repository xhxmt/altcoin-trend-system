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


def test_binance_exchange_info_parser_skips_nonfinite_numeric_values():
    adapter = BinancePublicAdapter()

    payload = {
        "symbols": [
            {
                "symbol": "SOLUSDT",
                "pair": "SOLUSDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "baseAsset": "SOL",
                "quoteAsset": "USDT",
                "onboardDate": 1710000000000,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "NaN"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.1"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            }
        ]
    }

    assert adapter.parse_exchange_info(payload) == []


def test_binance_exchange_info_parser_returns_empty_list_for_malformed_payload():
    adapter = BinancePublicAdapter()

    assert adapter.parse_exchange_info("not-a-mapping") == []
    assert adapter.parse_exchange_info({"symbols": "not-a-list"}) == []


def test_binance_exchange_info_parser_skips_bad_rows():
    adapter = BinancePublicAdapter()

    payload = {
        "symbols": [
            {
                "symbol": "SOLUSDT",
                "pair": "SOLUSDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "baseAsset": "SOL",
                "quoteAsset": "USDT",
                "onboardDate": 1710000000000,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.0100"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.1"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            },
            {
                "symbol": "SOLUSDT",
                "pair": "SOLUSDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "baseAsset": "SOL",
                "quoteAsset": "USDT",
                "onboardDate": "bad",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.0100"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.1"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            },
        ]
    }

    instruments = adapter.parse_exchange_info(payload)

    assert len(instruments) == 1
    assert instruments[0].symbol == "SOLUSDT"


def test_binance_exchange_info_parser_skips_non_dict_filter_entries():
    adapter = BinancePublicAdapter()

    payload = {
        "symbols": [
            {
                "symbol": "SOLUSDT",
                "pair": "SOLUSDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "baseAsset": "SOL",
                "quoteAsset": "USDT",
                "onboardDate": 1710000000000,
                "filters": [
                    "not-a-dict",
                    {"filterType": "PRICE_FILTER", "tickSize": "0.0100"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.1"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            }
        ]
    }

    instruments = adapter.parse_exchange_info(payload)

    assert len(instruments) == 1
    instrument = instruments[0]
    assert instrument.tick_size == 0.01
    assert instrument.step_size == 0.1
    assert instrument.min_notional == 5.0


def test_binance_exchange_info_parser_skips_non_string_symbol_fields():
    adapter = BinancePublicAdapter()

    payload = {
        "symbols": [
            {
                "symbol": "",
                "pair": "SOLUSDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "baseAsset": "SOL",
                "quoteAsset": "USDT",
                "onboardDate": 1710000000000,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.0100"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.1"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            }
        ]
    }

    assert adapter.parse_exchange_info(payload) == []


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


def test_binance_kline_ws_parser_rejects_non_1m_interval():
    adapter = BinancePublicAdapter()

    payload = load_json("binance_kline_ws.json")
    payload["data"]["k"]["i"] = "5m"

    assert adapter.parse_kline_message(payload) is None


def test_binance_kline_ws_parser_rejects_non_bool_close_flag():
    adapter = BinancePublicAdapter()

    base_payload = {
        "data": {
            "k": {
                "s": "SOLUSDT",
                "t": 1710000000000,
                "o": "100.0",
                "h": "102.0",
                "l": "99.5",
                "c": "101.0",
                "v": "1234.5",
                "q": "124000.5",
                "x": True,
            }
        }
    }

    for bad_close in ("false", 0, None):
        payload = json.loads(json.dumps(base_payload))
        payload["data"]["k"]["x"] = bad_close
        assert adapter.parse_kline_message(payload) is None


def test_binance_kline_ws_parser_returns_none_for_malformed_payload():
    adapter = BinancePublicAdapter()

    assert adapter.parse_kline_message({"data": []}) is None
    assert adapter.parse_kline_message({"data": "not-a-dict"}) is None
    assert adapter.parse_kline_message("not-a-mapping") is None
    assert adapter.parse_kline_message(load_json("binance_kline_ws.json"), symbol="") is None


def test_binance_kline_ws_parser_returns_none_for_missing_required_fields():
    adapter = BinancePublicAdapter()

    assert adapter.parse_kline_message({"data": {"k": {"o": "100.0", "t": 1710000000000}}}) is None
    assert adapter.parse_kline_message({"data": {"k": {"s": "SOLUSDT", "t": 1710000000000}}}) is None


def test_binance_kline_ws_parser_returns_none_for_bad_scalar_values():
    adapter = BinancePublicAdapter()

    payload = {
        "data": {
            "k": {
                "s": "SOLUSDT",
                "t": "bad",
                "o": "100.0",
                "h": "102.0",
                "l": "99.5",
                "c": "101.0",
                "v": "1234.5",
                "q": "124000.5",
                "x": True,
            }
        }
    }

    assert adapter.parse_kline_message(payload) is None


def test_binance_kline_ws_parser_rejects_nonfinite_numeric_values():
    adapter = BinancePublicAdapter()

    payload = {
        "data": {
            "k": {
                "s": "SOLUSDT",
                "t": 1710000000000,
                "o": "100.0",
                "h": "Infinity",
                "l": "99.5",
                "c": "101.0",
                "v": "1234.5",
                "q": "124000.5",
                "x": True,
            }
        }
    }

    assert adapter.parse_kline_message(payload) is None


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


def test_bybit_instruments_parser_skips_nonfinite_numeric_values():
    adapter = BybitPublicAdapter()

    payload = {
        "retCode": 0,
        "result": {
            "list": [
                {
                    "symbol": "SOLUSDT",
                    "status": "Trading",
                    "baseCoin": "SOL",
                    "quoteCoin": "USDT",
                    "launchTime": "1710000000000",
                    "contractType": "LinearPerpetual",
                    "priceFilter": {"tickSize": "Infinity"},
                    "lotSizeFilter": {"qtyStep": "0.1", "minNotionalValue": "5"},
                }
            ]
        },
    }

    assert adapter.parse_instruments_info(payload) == []


def test_bybit_instruments_parser_skips_non_string_symbol_fields():
    adapter = BybitPublicAdapter()

    payload = {
        "retCode": 0,
        "result": {
            "list": [
                {
                    "symbol": "",
                    "status": "Trading",
                    "baseCoin": "SOL",
                    "quoteCoin": "USDT",
                    "launchTime": "1710000000000",
                    "contractType": "LinearPerpetual",
                    "priceFilter": {"tickSize": "0.01"},
                    "lotSizeFilter": {"qtyStep": "0.1", "minNotionalValue": "5"},
                }
            ]
        },
    }

    assert adapter.parse_instruments_info(payload) == []


def test_bybit_instruments_parser_returns_empty_list_for_malformed_payload():
    adapter = BybitPublicAdapter()

    assert adapter.parse_instruments_info("not-a-mapping") == []
    assert adapter.parse_instruments_info({"result": "not-a-mapping"}) == []
    assert adapter.parse_instruments_info({"result": {"list": "not-a-list"}}) == []


def test_bybit_instruments_parser_skips_bad_rows():
    adapter = BybitPublicAdapter()

    payload = {
        "retCode": 0,
        "result": {
            "list": [
                {
                    "symbol": "SOLUSDT",
                    "status": "Trading",
                    "baseCoin": "SOL",
                    "quoteCoin": "USDT",
                    "launchTime": "1710000000000",
                    "contractType": "LinearPerpetual",
                    "priceFilter": {"tickSize": "0.01"},
                    "lotSizeFilter": {"qtyStep": "0.1", "minNotionalValue": "5"},
                },
                {
                    "symbol": "SOLUSDT",
                    "status": "Trading",
                    "baseCoin": "SOL",
                    "quoteCoin": "USDT",
                    "launchTime": "bad",
                    "contractType": "LinearPerpetual",
                    "priceFilter": {"tickSize": "0.01"},
                    "lotSizeFilter": {"qtyStep": "0.1", "minNotionalValue": "5"},
                },
            ]
        },
    }

    instruments = adapter.parse_instruments_info(payload)

    assert len(instruments) == 1
    assert instruments[0].symbol == "SOLUSDT"


def test_bybit_kline_ws_parser_returns_closed_bar():
    adapter = BybitPublicAdapter()

    bar = adapter.parse_kline_message(load_json("bybit_kline_ws.json"), symbol="SOLUSDT")

    assert bar is not None
    assert bar.exchange == "bybit"
    assert bar.symbol == "SOLUSDT"
    assert bar.close == 101.0
    assert bar.quote_volume == 124000.5
    assert bar.is_closed is True


def test_bybit_kline_ws_parser_rejects_non_1m_topic():
    adapter = BybitPublicAdapter()

    payload = load_json("bybit_kline_ws.json")
    payload["topic"] = "kline.5.SOLUSDT"

    assert adapter.parse_kline_message(payload) is None


def test_bybit_kline_ws_parser_rejects_non_bool_close_flag():
    adapter = BybitPublicAdapter()

    base_payload = {
        "topic": "kline.1.SOLUSDT",
        "data": [
            {
                "start": 1710000000000,
                "open": "100.0",
                "high": "102.0",
                "low": "99.5",
                "close": "101.0",
                "volume": "1234.5",
                "turnover": "124000.5",
                "confirm": True,
            }
        ],
    }

    for bad_close in ("false", 0, None):
        payload = json.loads(json.dumps(base_payload))
        payload["data"][0]["confirm"] = bad_close
        assert adapter.parse_kline_message(payload) is None


def test_bybit_kline_ws_parser_returns_none_without_topic_symbol():
    adapter = BybitPublicAdapter()

    assert adapter.parse_kline_message({"data": [{"start": 1710000000000}]}) is None
    assert adapter.parse_kline_message("not-a-mapping") is None


def test_bybit_kline_ws_parser_rejects_malformed_topic_or_symbol():
    adapter = BybitPublicAdapter()

    payload = {
        "topic": "kline.1.SOLUSDT",
        "data": [
            {
                "start": 1710000000000,
                "open": "100.0",
                "high": "102.0",
                "low": "99.5",
                "close": "101.0",
                "volume": "1234.5",
                "turnover": "124000.5",
                "confirm": True,
            }
        ],
    }

    assert adapter.parse_kline_message(payload, symbol="") is None
    assert adapter.parse_kline_message({**payload, "topic": "bad.topic"}) is None
    assert adapter.parse_kline_message({**payload, "topic": "kline.1."}) is None


def test_bybit_kline_ws_parser_returns_none_for_missing_required_fields():
    adapter = BybitPublicAdapter()

    assert adapter.parse_kline_message({"topic": "kline.1.SOLUSDT", "data": [{}]}) is None
    assert adapter.parse_kline_message({"topic": "kline.1.SOLUSDT", "data": {"start": 1710000000000}}) is None


def test_bybit_kline_ws_parser_returns_none_for_bad_scalar_values():
    adapter = BybitPublicAdapter()

    payload = {
        "topic": "kline.1.SOLUSDT",
        "data": [
            {
                "start": "bad",
                "open": "100.0",
                "high": "102.0",
                "low": "99.5",
                "close": "101.0",
                "volume": "1234.5",
                "turnover": "124000.5",
                "confirm": True,
            }
        ],
    }

    assert adapter.parse_kline_message(payload) is None
