import json
from pathlib import Path

import pytest

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


def test_binance_list_usdt_perp_symbols_fetches_exchange_info(monkeypatch):
    adapter = BinancePublicAdapter()
    captured = {}
    response = _FakeResponse(
        {
            "symbols": [
                {
                    "symbol": "SOLUSDT",
                    "pair": "SOLUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "baseAsset": "SOL",
                    "quoteAsset": "USDT",
                    "onboardDate": 1710000000000,
                    "filters": [],
                },
                {
                    "symbol": "ETHBUSD",
                    "pair": "ETHBUSD",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "baseAsset": "ETH",
                    "quoteAsset": "BUSD",
                    "onboardDate": 1710000000000,
                    "filters": [],
                },
            ]
        }
    )

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr("altcoin_trend.exchanges.binance.httpx.get", fake_get)

    assert adapter.list_usdt_perp_symbols() == ["SOLUSDT"]
    assert captured == {
        "url": "https://fapi.binance.com/fapi/v1/exchangeInfo",
        "timeout": 20,
    }
    assert response.raise_called is True


def test_binance_fetch_klines_1m_paginates_until_end(monkeypatch):
    adapter = BinancePublicAdapter()
    calls = []

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    pages = [
        [
            [1000, "1", "1", "1", "1", "10", 0, "10", 1, "5", "5"],
            [61000, "2", "2", "2", "2", "20", 0, "40", 2, "10", "20"],
        ],
        [
            [121000, "3", "3", "3", "3", "30", 0, "90", 3, "15", "45"],
        ],
    ]

    def fake_get(url, params, timeout):
        calls.append(params.copy())
        return Response(pages[len(calls) - 1])

    monkeypatch.setattr("altcoin_trend.exchanges.binance.httpx.get", fake_get)

    bars = adapter.fetch_klines_1m("SOLUSDT", start_ms=1000, end_ms=181000)

    assert [bar.ts.timestamp() for bar in bars] == [1.0, 61.0, 121.0]
    assert [call["startTime"] for call in calls] == [1000, 121000]
    assert all(call["limit"] == 1500 for call in calls)


def test_bybit_fetch_klines_1m_paginates_and_sorts(monkeypatch):
    adapter = BybitPublicAdapter()
    calls = []

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    pages = [
        {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    ["61000", "2", "2", "2", "2", "20", "40"],
                    ["1000", "1", "1", "1", "1", "10", "10"],
                ]
            },
        },
        {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    ["121000", "3", "3", "3", "3", "30", "90"],
                ]
            },
        },
    ]

    def fake_get(url, params, timeout):
        calls.append(params.copy())
        return Response(pages[len(calls) - 1])

    monkeypatch.setattr("altcoin_trend.exchanges.bybit.httpx.get", fake_get)

    bars = adapter.fetch_klines_1m("SOLUSDT", start_ms=1000, end_ms=181000)

    assert [bar.ts.timestamp() for bar in bars] == [1.0, 61.0, 121.0]
    assert [call["start"] for call in calls] == [1000, 121000]
    assert all(call["limit"] == 1000 for call in calls)


def test_binance_derivatives_parsers_normalize_funding_and_open_interest():
    adapter = BinancePublicAdapter()

    funding = adapter.parse_funding_history(
        [
            {"symbol": "SOLUSDT", "fundingRate": "0.0001", "fundingTime": 1710000000000},
            {"symbol": "SOLUSDT", "fundingRate": "bad", "fundingTime": 1710003600000},
        ]
    )
    oi = adapter.parse_open_interest_history(
        [
            {
                "symbol": "SOLUSDT",
                "sumOpenInterest": "123.4",
                "sumOpenInterestValue": "5678.9",
                "timestamp": "1710000000000",
            }
        ]
    )

    assert len(funding) == 1
    assert funding[0].exchange == "binance"
    assert funding[0].symbol == "SOLUSDT"
    assert funding[0].funding_rate == 0.0001
    assert len(oi) == 1
    assert oi[0].open_interest == 123.4
    assert oi[0].open_interest_value == 5678.9


def test_bybit_derivatives_parsers_normalize_funding_oi_and_long_short():
    adapter = BybitPublicAdapter()

    funding = adapter.parse_funding_history(
        {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {"symbol": "SOLUSDT", "fundingRate": "0.0002", "fundingRateTimestamp": "1710000000000"}
                ]
            },
        }
    )
    oi = adapter.parse_open_interest_history(
        {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"list": [{"openInterest": "234.5", "timestamp": "1710000000000"}]},
        },
        symbol="SOLUSDT",
    )
    ratios = adapter.parse_long_short_ratio_history(
        {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {"symbol": "SOLUSDT", "buyRatio": "0.54", "sellRatio": "0.46", "timestamp": "1710000000000"}
                ]
            },
        }
    )

    assert funding[0].exchange == "bybit"
    assert funding[0].funding_rate == 0.0002
    assert oi[0].open_interest == 234.5
    assert ratios[0].long_short_ratio == 0.54 / 0.46


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


def test_binance_kline_ws_parser_rejects_mismatched_expected_symbol():
    adapter = BinancePublicAdapter()

    assert adapter.parse_kline_message(load_json("binance_kline_ws.json"), symbol="BTCUSDT") is None


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


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.raise_called = False

    def raise_for_status(self):
        self.raise_called = True

    def json(self):
        return self.payload


def test_binance_fetch_klines_1m_calls_http_and_parses_rows(monkeypatch):
    adapter = BinancePublicAdapter()
    captured = {}
    response = _FakeResponse(
        [
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
    )

    def fake_get(url, params, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr("altcoin_trend.exchanges.binance.httpx.get", fake_get)

    bars = adapter.fetch_klines_1m("SOLUSDT", 1, 2)

    assert captured == {
        "url": "https://fapi.binance.com/fapi/v1/klines",
        "params": {
            "symbol": "SOLUSDT",
            "interval": "1m",
            "startTime": 1,
            "endTime": 2,
            "limit": 1500,
        },
        "timeout": 20,
    }
    assert response.raise_called is True
    assert bars[0].symbol == "SOLUSDT"
    assert bars[0].close == 101.0


def test_binance_fetch_klines_1m_rejects_malformed_payload(monkeypatch):
    adapter = BinancePublicAdapter()

    monkeypatch.setattr("altcoin_trend.exchanges.binance.httpx.get", lambda *args, **kwargs: _FakeResponse({"bad": "payload"}))

    with pytest.raises(ValueError, match="Malformed Binance klines response"):
        adapter.fetch_klines_1m("SOLUSDT", 1, 2)


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


def test_bybit_list_usdt_perp_symbols_fetches_all_pages(monkeypatch):
    adapter = BybitPublicAdapter()
    calls = []
    responses = [
        _FakeResponse(
            {
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "nextPageCursor": "next",
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
                        }
                    ],
                },
            }
        ),
        _FakeResponse(
            {
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "nextPageCursor": "",
                    "list": [
                        {
                            "symbol": "ARBUSDT",
                            "status": "Trading",
                            "baseCoin": "ARB",
                            "quoteCoin": "USDT",
                            "launchTime": "1710000000000",
                            "contractType": "LinearPerpetual",
                            "priceFilter": {"tickSize": "0.0001"},
                            "lotSizeFilter": {"qtyStep": "0.1", "minNotionalValue": "5"},
                        }
                    ],
                },
            }
        ),
    ]

    def fake_get(url, params, timeout):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return responses[len(calls) - 1]

    monkeypatch.setattr("altcoin_trend.exchanges.bybit.httpx.get", fake_get)

    assert adapter.list_usdt_perp_symbols() == ["SOLUSDT", "ARBUSDT"]
    assert calls == [
        {
            "url": "https://api.bybit.com/v5/market/instruments-info",
            "params": {"category": "linear", "limit": 1000},
            "timeout": 20,
        },
        {
            "url": "https://api.bybit.com/v5/market/instruments-info",
            "params": {"category": "linear", "limit": 1000, "cursor": "next"},
            "timeout": 20,
        },
    ]
    assert all(response.raise_called for response in responses)


def test_bybit_list_usdt_perp_symbols_raises_for_nonzero_retcode(monkeypatch):
    adapter = BybitPublicAdapter()

    monkeypatch.setattr(
        "altcoin_trend.exchanges.bybit.httpx.get",
        lambda *args, **kwargs: _FakeResponse({"retCode": 10001, "retMsg": "bad request", "result": {}}),
    )

    with pytest.raises(ValueError, match="Bybit instruments request failed: retCode=10001 retMsg=bad request"):
        adapter.list_usdt_perp_symbols()


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


def test_bybit_kline_ws_parser_rejects_non_1m_topic_even_with_symbol():
    adapter = BybitPublicAdapter()

    payload = load_json("bybit_kline_ws.json")
    payload["topic"] = "kline.5.SOLUSDT"

    assert adapter.parse_kline_message(payload, symbol="SOLUSDT") is None


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


def test_bybit_fetch_klines_1m_calls_http_and_returns_ascending_bars(monkeypatch):
    adapter = BybitPublicAdapter()
    captured = {}
    response = _FakeResponse(
        {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    ["1710000060000", "101.0", "103.0", "100.0", "102.0", "2000.0", "204000.0"],
                    ["1710000000000", "100.0", "102.0", "99.5", "101.0", "1234.5", "124000.5"],
                ]
            },
        }
    )

    def fake_get(url, params, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr("altcoin_trend.exchanges.bybit.httpx.get", fake_get)

    bars = adapter.fetch_klines_1m("SOLUSDT", 1, 2)

    assert captured == {
        "url": "https://api.bybit.com/v5/market/kline",
        "params": {
            "category": "linear",
            "symbol": "SOLUSDT",
            "interval": "1",
            "start": 1,
            "end": 2,
            "limit": 1000,
        },
        "timeout": 20,
    }
    assert response.raise_called is True
    assert [bar.ts for bar in bars] == sorted(bar.ts for bar in bars)
    assert [bar.close for bar in bars] == [101.0, 102.0]


def test_bybit_fetch_klines_1m_raises_for_nonzero_retcode(monkeypatch):
    adapter = BybitPublicAdapter()

    monkeypatch.setattr(
        "altcoin_trend.exchanges.bybit.httpx.get",
        lambda *args, **kwargs: _FakeResponse({"retCode": 10001, "retMsg": "bad request", "result": {}}),
    )

    with pytest.raises(ValueError, match="Bybit kline request failed: retCode=10001 retMsg=bad request"):
        adapter.fetch_klines_1m("SOLUSDT", 1, 2)
