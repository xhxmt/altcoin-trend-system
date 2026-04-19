# Backfill Pagination and Derivatives Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make historical backfill complete and idempotent, then replace fixed derivatives scoring with public OI/funding/taker-confirmation signals.

**Architecture:** Exchange adapters will page kline and low-frequency derivatives REST endpoints into normalized dataclasses. Bootstrap will use a conflict-tolerant market insert helper. Scheduler will compute derivatives features from existing `market_1m` nullable columns and existing taker volume fields, then explain will render the new fields.

**Tech Stack:** Python 3.12, Typer, SQLAlchemy, pandas, httpx, pytest, PostgreSQL.

---

## File Structure

- Modify `src/altcoin_trend/models.py`
  - Add normalized dataclasses for derivatives observations.
- Modify `src/altcoin_trend/exchanges/binance.py`
  - Add paginated 1m kline fetch, funding parser/fetcher, OI parser/fetcher.
- Modify `src/altcoin_trend/exchanges/bybit.py`
  - Add paginated 1m kline fetch, funding parser/fetcher, OI parser/fetcher, long/short parser/fetcher.
- Modify `src/altcoin_trend/db.py`
  - Add `insert_market_rows_ignore_conflicts`.
- Modify `src/altcoin_trend/ingest/bootstrap.py`
  - Use conflict-tolerant market inserts.
- Create `src/altcoin_trend/ingest/derivatives.py`
  - Normalize derivative observations into `UPDATE alt_core.market_1m` rows.
- Create `src/altcoin_trend/features/derivatives.py` replacement behavior
  - Own derivatives feature math and score calculation.
- Modify `src/altcoin_trend/scheduler.py`
  - Add derivatives feature fields and data-driven `derivatives_score`.
- Modify `src/altcoin_trend/signals/explain.py`
  - Render derivatives values with `n/a`.
- Modify `src/altcoin_trend/cli.py`
  - Add `bootstrap-derivatives --lookback-days`.
- Modify targeted tests:
  - `tests/test_exchange_contracts.py`
  - `tests/test_db_migrations.py`
  - `tests/test_bootstrap.py`
  - `tests/test_derivatives.py`
  - `tests/test_scheduler.py`
  - `tests/test_scoring.py`
  - `tests/test_cli.py`

## Task 1: Paginated Kline Fetching

**Files:**
- Modify: `tests/test_exchange_contracts.py`
- Modify: `src/altcoin_trend/exchanges/binance.py`
- Modify: `src/altcoin_trend/exchanges/bybit.py`

- [ ] **Step 1: Write failing Binance pagination test**

Append to `tests/test_exchange_contracts.py`:

```python
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
```

- [ ] **Step 2: Write failing Bybit pagination test**

Append to `tests/test_exchange_contracts.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_exchange_contracts.py::test_binance_fetch_klines_1m_paginates_until_end tests/test_exchange_contracts.py::test_bybit_fetch_klines_1m_paginates_and_sorts -q
```

Expected: FAIL because each adapter only fetches one page.

- [ ] **Step 4: Implement Binance pagination**

In `src/altcoin_trend/exchanges/binance.py`, replace `fetch_klines_1m` with:

```python
    def fetch_klines_1m(self, symbol: str, start_ms: int, end_ms: int) -> list[MarketBar1m]:
        bars: list[MarketBar1m] = []
        next_start = start_ms
        while next_start <= end_ms:
            response = httpx.get(
                f"{self.base_url}/fapi/v1/klines",
                params={
                    "symbol": symbol,
                    "interval": "1m",
                    "startTime": next_start,
                    "endTime": end_ms,
                    "limit": 1500,
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("Malformed Binance klines response: payload must be a list")
            page = self.parse_rest_klines(symbol, payload)
            if not page:
                break
            bars.extend(page)
            last_ms = int(page[-1].ts.timestamp() * 1000)
            advanced_start = last_ms + 60_000
            if advanced_start <= next_start:
                break
            next_start = advanced_start
            if last_ms >= end_ms:
                break
        return bars
```

- [ ] **Step 5: Implement Bybit pagination**

In `src/altcoin_trend/exchanges/bybit.py`, replace `fetch_klines_1m` with:

```python
    def fetch_klines_1m(self, symbol: str, start_ms: int, end_ms: int) -> list[MarketBar1m]:
        bars: list[MarketBar1m] = []
        next_start = start_ms
        while next_start <= end_ms:
            response = httpx.get(
                f"{self.base_url}/v5/market/kline",
                params={
                    "category": "linear",
                    "symbol": symbol,
                    "interval": "1",
                    "start": next_start,
                    "end": end_ms,
                    "limit": 1000,
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Malformed Bybit klines response: payload must be a mapping")

            ret_code = payload.get("retCode")
            ret_msg = payload.get("retMsg")
            if ret_code != 0:
                raise ValueError(f"Bybit kline request failed: retCode={ret_code} retMsg={ret_msg}")

            result = payload.get("result")
            if not isinstance(result, dict):
                raise ValueError("Malformed Bybit klines response: missing result mapping")
            rows = result.get("list", [])
            if not isinstance(rows, list):
                raise ValueError("Malformed Bybit klines response: result.list must be a list")

            page = sorted(self.parse_rest_klines(symbol, rows), key=lambda bar: bar.ts)
            if not page:
                break
            bars.extend(page)
            last_ms = int(page[-1].ts.timestamp() * 1000)
            advanced_start = last_ms + 60_000
            if advanced_start <= next_start:
                break
            next_start = advanced_start
            if last_ms >= end_ms:
                break
        return sorted(bars, key=lambda bar: bar.ts)
```

- [ ] **Step 6: Run pagination tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/test_exchange_contracts.py::test_binance_fetch_klines_1m_paginates_until_end tests/test_exchange_contracts.py::test_bybit_fetch_klines_1m_paginates_and_sorts -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

Run:

```bash
git add tests/test_exchange_contracts.py src/altcoin_trend/exchanges/binance.py src/altcoin_trend/exchanges/bybit.py
git commit -m "feat: paginate historical kline fetches"
```

Expected: commit succeeds.

## Task 2: Idempotent Market Bar Inserts

**Files:**
- Modify: `tests/test_db_migrations.py`
- Modify: `tests/test_bootstrap.py`
- Modify: `src/altcoin_trend/db.py`
- Modify: `src/altcoin_trend/ingest/bootstrap.py`

- [ ] **Step 1: Write failing db helper test**

Append to `tests/test_db_migrations.py`:

```python
def test_insert_market_rows_ignore_conflicts_uses_market_primary_key():
    engine = _InsertFakeEngine()
    rows = [
        {
            "asset_id": 1,
            "exchange": "binance",
            "symbol": "SOLUSDT",
            "ts": "2026-01-01T00:00:00Z",
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1.0,
            "quote_volume": 1.0,
            "trade_count": None,
            "taker_buy_base": None,
            "taker_buy_quote": None,
            "data_status": "healthy",
            "reason_codes": [],
        }
    ]

    count = db.insert_market_rows_ignore_conflicts(engine, rows)

    assert count == 1
    statement, recorded_rows = engine.statements[0]
    assert "INSERT INTO alt_core.market_1m" in statement
    assert "ON CONFLICT (asset_id, ts) DO NOTHING" in statement
    assert recorded_rows == rows
```

- [ ] **Step 2: Write failing bootstrap usage test**

In `tests/test_bootstrap.py`, update `test_bootstrap_exchange_filters_fetches_and_writes_market_rows` monkeypatches:

```python
    def fake_insert_market_rows_ignore_conflicts(engine, rows):
        inserted.append(("alt_core.market_1m", list(rows)))
        return len(inserted[-1][1])

    monkeypatch.setattr(bootstrap_module, "insert_market_rows_ignore_conflicts", fake_insert_market_rows_ignore_conflicts)
```

Remove or stop using the old monkeypatch of `bootstrap_module.insert_rows` in this test.

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_db_migrations.py::test_insert_market_rows_ignore_conflicts_uses_market_primary_key tests/test_bootstrap.py::test_bootstrap_exchange_filters_fetches_and_writes_market_rows -q
```

Expected: FAIL because helper does not exist and bootstrap still imports `insert_rows`.

- [ ] **Step 4: Implement db helper**

In `src/altcoin_trend/db.py`, add this function after `insert_rows`:

```python
def insert_market_rows_ignore_conflicts(engine: Engine, rows: Iterable[dict]) -> int:
    rows = list(rows)
    if not rows:
        return 0

    first_row_keys = set(rows[0].keys())
    for row in rows[1:]:
        if set(row.keys()) != first_row_keys:
            raise ValueError("All rows must have the same key set")

    columns = list(rows[0].keys())
    if any(not isinstance(column, str) or _SAFE_IDENTIFIER_RE.fullmatch(column) is None for column in columns):
        raise ValueError("Invalid column name in market rows")
    column_sql = ", ".join(columns)
    placeholder_sql = ", ".join(f":{column}" for column in columns)
    statement = text(
        f"INSERT INTO alt_core.market_1m ({column_sql}) VALUES ({placeholder_sql}) "
        "ON CONFLICT (asset_id, ts) DO NOTHING"
    )

    with engine.begin() as connection:
        result = connection.execute(statement, rows)
        rowcount = getattr(result, "rowcount", None)
    return int(rowcount) if rowcount is not None and rowcount >= 0 else len(rows)
```

- [ ] **Step 5: Update bootstrap to use helper**

In `src/altcoin_trend/ingest/bootstrap.py`, change the import:

```python
from altcoin_trend.db import insert_market_rows_ignore_conflicts, upsert_instruments
```

Change the write line:

```python
        bars_written += insert_market_rows_ignore_conflicts(engine, rows)
```

- [ ] **Step 6: Run db/bootstrap tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/test_db_migrations.py::test_insert_market_rows_ignore_conflicts_uses_market_primary_key tests/test_bootstrap.py::test_bootstrap_exchange_filters_fetches_and_writes_market_rows -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

Run:

```bash
git add tests/test_db_migrations.py tests/test_bootstrap.py src/altcoin_trend/db.py src/altcoin_trend/ingest/bootstrap.py
git commit -m "feat: make market backfill idempotent"
```

Expected: commit succeeds.

## Task 3: Derivatives Models and Exchange Parsers

**Files:**
- Modify: `src/altcoin_trend/models.py`
- Modify: `tests/test_exchange_contracts.py`
- Modify: `src/altcoin_trend/exchanges/binance.py`
- Modify: `src/altcoin_trend/exchanges/bybit.py`

- [ ] **Step 1: Add failing parser tests**

Append to `tests/test_exchange_contracts.py`:

```python
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
            {"symbol": "SOLUSDT", "sumOpenInterest": "123.4", "sumOpenInterestValue": "5678.9", "timestamp": "1710000000000"}
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
            "result": {"list": [{"symbol": "SOLUSDT", "fundingRate": "0.0002", "fundingRateTimestamp": "1710000000000"}]},
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
            "result": {"list": [{"symbol": "SOLUSDT", "buyRatio": "0.54", "sellRatio": "0.46", "timestamp": "1710000000000"}]},
        }
    )

    assert funding[0].exchange == "bybit"
    assert funding[0].funding_rate == 0.0002
    assert oi[0].open_interest == 234.5
    assert ratios[0].long_short_ratio == 0.54 / 0.46
```

- [ ] **Step 2: Run parser tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_exchange_contracts.py::test_binance_derivatives_parsers_normalize_funding_and_open_interest tests/test_exchange_contracts.py::test_bybit_derivatives_parsers_normalize_funding_oi_and_long_short -q
```

Expected: FAIL because parser methods and dataclasses do not exist.

- [ ] **Step 3: Add dataclasses**

In `src/altcoin_trend/models.py`, add:

```python
@dataclass(frozen=True)
class FundingRateObservation:
    exchange: str
    symbol: str
    ts: datetime
    funding_rate: float


@dataclass(frozen=True)
class OpenInterestObservation:
    exchange: str
    symbol: str
    ts: datetime
    open_interest: float
    open_interest_value: float | None = None


@dataclass(frozen=True)
class LongShortRatioObservation:
    exchange: str
    symbol: str
    ts: datetime
    long_short_ratio: float
    buy_ratio: float | None = None
    sell_ratio: float | None = None
```

- [ ] **Step 4: Implement Binance parsers**

In `src/altcoin_trend/exchanges/binance.py`, import:

```python
from altcoin_trend.models import FundingRateObservation, Instrument, MarketBar1m, OpenInterestObservation, utc_from_ms
```

Add methods to `BinancePublicAdapter`:

```python
    def parse_funding_history(self, rows: list[dict]) -> list[FundingRateObservation]:
        observations: list[FundingRateObservation] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                symbol = _nonempty_str(row.get("symbol"))
                if symbol is None:
                    continue
                observations.append(
                    FundingRateObservation(
                        exchange=self.exchange,
                        symbol=symbol,
                        ts=utc_from_ms(int(row["fundingTime"])),
                        funding_rate=_finite_float(row["fundingRate"]),
                    )
                )
            except (TypeError, ValueError, KeyError):
                continue
        return observations

    def parse_open_interest_history(self, rows: list[dict]) -> list[OpenInterestObservation]:
        observations: list[OpenInterestObservation] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                symbol = _nonempty_str(row.get("symbol"))
                if symbol is None:
                    continue
                observations.append(
                    OpenInterestObservation(
                        exchange=self.exchange,
                        symbol=symbol,
                        ts=utc_from_ms(int(row["timestamp"])),
                        open_interest=_finite_float(row["sumOpenInterest"]),
                        open_interest_value=_finite_float(row["sumOpenInterestValue"]) if row.get("sumOpenInterestValue") is not None else None,
                    )
                )
            except (TypeError, ValueError, KeyError):
                continue
        return observations
```

- [ ] **Step 5: Implement Bybit parsers**

In `src/altcoin_trend/exchanges/bybit.py`, import:

```python
from altcoin_trend.models import FundingRateObservation, Instrument, LongShortRatioObservation, MarketBar1m, OpenInterestObservation, utc_from_ms
```

Add this helper near the top:

```python
def _bybit_result_list(payload: dict) -> list:
    if not isinstance(payload, dict) or payload.get("retCode") != 0:
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    rows = result.get("list", [])
    return rows if isinstance(rows, list) else []
```

Add methods to `BybitPublicAdapter`:

```python
    def parse_funding_history(self, payload: dict) -> list[FundingRateObservation]:
        observations: list[FundingRateObservation] = []
        for row in _bybit_result_list(payload):
            if not isinstance(row, dict):
                continue
            try:
                symbol = _nonempty_str(row.get("symbol"))
                if symbol is None:
                    continue
                observations.append(
                    FundingRateObservation(
                        exchange=self.exchange,
                        symbol=symbol,
                        ts=utc_from_ms(int(row["fundingRateTimestamp"])),
                        funding_rate=_finite_float(row["fundingRate"]),
                    )
                )
            except (TypeError, ValueError, KeyError):
                continue
        return observations

    def parse_open_interest_history(self, payload: dict, symbol: str) -> list[OpenInterestObservation]:
        observations: list[OpenInterestObservation] = []
        for row in _bybit_result_list(payload):
            if not isinstance(row, dict):
                continue
            try:
                observations.append(
                    OpenInterestObservation(
                        exchange=self.exchange,
                        symbol=symbol,
                        ts=utc_from_ms(int(row["timestamp"])),
                        open_interest=_finite_float(row["openInterest"]),
                        open_interest_value=None,
                    )
                )
            except (TypeError, ValueError, KeyError):
                continue
        return observations

    def parse_long_short_ratio_history(self, payload: dict) -> list[LongShortRatioObservation]:
        observations: list[LongShortRatioObservation] = []
        for row in _bybit_result_list(payload):
            if not isinstance(row, dict):
                continue
            try:
                symbol = _nonempty_str(row.get("symbol"))
                if symbol is None:
                    continue
                buy_ratio = _finite_float(row["buyRatio"])
                sell_ratio = _finite_float(row["sellRatio"])
                if sell_ratio <= 0:
                    continue
                observations.append(
                    LongShortRatioObservation(
                        exchange=self.exchange,
                        symbol=symbol,
                        ts=utc_from_ms(int(row["timestamp"])),
                        long_short_ratio=buy_ratio / sell_ratio,
                        buy_ratio=buy_ratio,
                        sell_ratio=sell_ratio,
                    )
                )
            except (TypeError, ValueError, KeyError):
                continue
        return observations
```

- [ ] **Step 6: Run parser tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/test_exchange_contracts.py::test_binance_derivatives_parsers_normalize_funding_and_open_interest tests/test_exchange_contracts.py::test_bybit_derivatives_parsers_normalize_funding_oi_and_long_short -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

Run:

```bash
git add tests/test_exchange_contracts.py src/altcoin_trend/models.py src/altcoin_trend/exchanges/binance.py src/altcoin_trend/exchanges/bybit.py
git commit -m "feat: parse public derivatives observations"
```

Expected: commit succeeds.

## Task 4: Public Derivatives History Fetchers

**Files:**
- Modify: `tests/test_exchange_contracts.py`
- Modify: `src/altcoin_trend/exchanges/binance.py`
- Modify: `src/altcoin_trend/exchanges/bybit.py`

- [ ] **Step 1: Write failing Binance fetcher test**

Append to `tests/test_exchange_contracts.py`:

```python
def test_binance_derivatives_fetchers_call_public_endpoints(monkeypatch):
    adapter = BinancePublicAdapter()
    calls = []

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, params, timeout):
        calls.append((url, params.copy(), timeout))
        if url.endswith("/fapi/v1/fundingRate"):
            return Response([{"symbol": "SOLUSDT", "fundingRate": "0.0001", "fundingTime": 1710000000000}])
        if url.endswith("/futures/data/openInterestHist"):
            return Response([{"symbol": "SOLUSDT", "sumOpenInterest": "123.4", "sumOpenInterestValue": "5678.9", "timestamp": "1710000000000"}])
        raise AssertionError(url)

    monkeypatch.setattr("altcoin_trend.exchanges.binance.httpx.get", fake_get)

    funding = adapter.fetch_funding_rate_history("SOLUSDT", 1000, 2000)
    oi = adapter.fetch_open_interest_history("SOLUSDT", 1000, 2000, "1h")

    assert funding[0].funding_rate == 0.0001
    assert oi[0].open_interest == 123.4
    assert calls[0][0].endswith("/fapi/v1/fundingRate")
    assert calls[0][1] == {"symbol": "SOLUSDT", "startTime": 1000, "endTime": 2000, "limit": 1000}
    assert calls[1][0].endswith("/futures/data/openInterestHist")
    assert calls[1][1] == {"symbol": "SOLUSDT", "period": "1h", "startTime": 1000, "endTime": 2000, "limit": 500}
```

- [ ] **Step 2: Write failing Bybit fetcher test**

Append to `tests/test_exchange_contracts.py`:

```python
def test_bybit_derivatives_fetchers_call_public_endpoints(monkeypatch):
    adapter = BybitPublicAdapter()
    calls = []

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, params, timeout):
        calls.append((url, params.copy(), timeout))
        if url.endswith("/v5/market/funding/history"):
            return Response({"retCode": 0, "retMsg": "OK", "result": {"list": [{"symbol": "SOLUSDT", "fundingRate": "0.0002", "fundingRateTimestamp": "1710000000000"}]}})
        if url.endswith("/v5/market/open-interest"):
            return Response({"retCode": 0, "retMsg": "OK", "result": {"list": [{"openInterest": "234.5", "timestamp": "1710000000000"}], "nextPageCursor": ""}})
        if url.endswith("/v5/market/account-ratio"):
            return Response({"retCode": 0, "retMsg": "OK", "result": {"list": [{"symbol": "SOLUSDT", "buyRatio": "0.54", "sellRatio": "0.46", "timestamp": "1710000000000"}], "nextPageCursor": ""}})
        raise AssertionError(url)

    monkeypatch.setattr("altcoin_trend.exchanges.bybit.httpx.get", fake_get)

    funding = adapter.fetch_funding_rate_history("SOLUSDT", 1000, 2000)
    oi = adapter.fetch_open_interest_history("SOLUSDT", 1000, 2000, "1h")
    ratios = adapter.fetch_long_short_ratio_history("SOLUSDT", 1000, 2000, "1h")

    assert funding[0].funding_rate == 0.0002
    assert oi[0].open_interest == 234.5
    assert ratios[0].long_short_ratio == 0.54 / 0.46
    assert calls[0][1] == {"category": "linear", "symbol": "SOLUSDT", "startTime": 1000, "endTime": 2000, "limit": 200}
    assert calls[1][1] == {"category": "linear", "symbol": "SOLUSDT", "intervalTime": "1h", "startTime": 1000, "endTime": 2000, "limit": 200}
    assert calls[2][1] == {"category": "linear", "symbol": "SOLUSDT", "period": "1h", "startTime": "1000", "endTime": "2000", "limit": 500}
```

- [ ] **Step 3: Run fetcher tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_exchange_contracts.py::test_binance_derivatives_fetchers_call_public_endpoints tests/test_exchange_contracts.py::test_bybit_derivatives_fetchers_call_public_endpoints -q
```

Expected: FAIL because fetcher methods do not exist.

- [ ] **Step 4: Implement Binance fetchers**

Add to `BinancePublicAdapter` in `src/altcoin_trend/exchanges/binance.py`:

```python
    def fetch_funding_rate_history(self, symbol: str, start_ms: int, end_ms: int) -> list[FundingRateObservation]:
        response = httpx.get(
            f"{self.base_url}/fapi/v1/fundingRate",
            params={"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Malformed Binance funding response: payload must be a list")
        return self.parse_funding_history(payload)

    def fetch_open_interest_history(
        self,
        symbol: str,
        start_ms: int,
        end_ms: int,
        period: str = "1h",
    ) -> list[OpenInterestObservation]:
        response = httpx.get(
            f"{self.base_url}/futures/data/openInterestHist",
            params={"symbol": symbol, "period": period, "startTime": start_ms, "endTime": end_ms, "limit": 500},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Malformed Binance open interest response: payload must be a list")
        return self.parse_open_interest_history(payload)
```

- [ ] **Step 5: Implement Bybit fetchers**

Add to `BybitPublicAdapter` in `src/altcoin_trend/exchanges/bybit.py`:

```python
    def fetch_funding_rate_history(self, symbol: str, start_ms: int, end_ms: int) -> list[FundingRateObservation]:
        response = httpx.get(
            f"{self.base_url}/v5/market/funding/history",
            params={"category": "linear", "symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 200},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Malformed Bybit funding response: payload must be a mapping")
        return self.parse_funding_history(payload)

    def fetch_open_interest_history(
        self,
        symbol: str,
        start_ms: int,
        end_ms: int,
        period: str = "1h",
    ) -> list[OpenInterestObservation]:
        observations: list[OpenInterestObservation] = []
        cursor: str | None = None
        while True:
            params = {
                "category": "linear",
                "symbol": symbol,
                "intervalTime": period,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor
            response = httpx.get(f"{self.base_url}/v5/market/open-interest", params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Malformed Bybit open interest response: payload must be a mapping")
            observations.extend(self.parse_open_interest_history(payload, symbol=symbol))
            result = payload.get("result")
            next_cursor = result.get("nextPageCursor") if isinstance(result, dict) else None
            if not isinstance(next_cursor, str) or not next_cursor.strip():
                break
            cursor = next_cursor
        return observations

    def fetch_long_short_ratio_history(
        self,
        symbol: str,
        start_ms: int,
        end_ms: int,
        period: str = "1h",
    ) -> list[LongShortRatioObservation]:
        observations: list[LongShortRatioObservation] = []
        cursor: str | None = None
        while True:
            params = {
                "category": "linear",
                "symbol": symbol,
                "period": period,
                "startTime": str(start_ms),
                "endTime": str(end_ms),
                "limit": 500,
            }
            if cursor:
                params["cursor"] = cursor
            response = httpx.get(f"{self.base_url}/v5/market/account-ratio", params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Malformed Bybit long short response: payload must be a mapping")
            observations.extend(self.parse_long_short_ratio_history(payload))
            result = payload.get("result")
            next_cursor = result.get("nextPageCursor") if isinstance(result, dict) else None
            if not isinstance(next_cursor, str) or not next_cursor.strip():
                break
            cursor = next_cursor
        return observations
```

- [ ] **Step 6: Run fetcher tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/test_exchange_contracts.py::test_binance_derivatives_fetchers_call_public_endpoints tests/test_exchange_contracts.py::test_bybit_derivatives_fetchers_call_public_endpoints -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

Run:

```bash
git add tests/test_exchange_contracts.py src/altcoin_trend/exchanges/binance.py src/altcoin_trend/exchanges/bybit.py
git commit -m "feat: fetch public derivatives history"
```

Expected: commit succeeds.

## Task 5: Derivatives Feature Scoring

**Files:**
- Create: `tests/test_derivatives.py`
- Modify: `src/altcoin_trend/features/derivatives.py`

- [ ] **Step 1: Write failing derivatives feature tests**

Create `tests/test_derivatives.py`:

```python
import pandas as pd

from altcoin_trend.features.derivatives import compute_derivatives_features


def test_derivatives_features_return_neutral_without_derivatives_data():
    frame = pd.DataFrame(
        [
            {"ts": "2026-01-01T00:00:00Z", "close": 100.0, "quote_volume": 1000.0},
            {"ts": "2026-01-01T01:00:00Z", "close": 105.0, "quote_volume": 1000.0},
        ]
    )

    features = compute_derivatives_features(frame)

    assert features.derivatives_score == 50.0
    assert features.oi_delta_1h is None
    assert features.funding_zscore is None


def test_derivatives_features_reward_price_up_with_open_interest_up():
    frame = pd.DataFrame(
        [
            {"ts": "2026-01-01T00:00:00Z", "close": 100.0, "open_interest": 1000.0, "funding_rate": 0.0001, "quote_volume": 1000.0, "taker_buy_quote": 520.0},
            {"ts": "2026-01-01T01:00:00Z", "close": 105.0, "open_interest": 1100.0, "funding_rate": 0.0001, "quote_volume": 1000.0, "taker_buy_quote": 560.0},
            {"ts": "2026-01-01T04:00:00Z", "close": 112.0, "open_interest": 1250.0, "funding_rate": 0.00012, "quote_volume": 1000.0, "taker_buy_quote": 570.0},
        ]
    )

    features = compute_derivatives_features(frame)

    assert features.oi_delta_1h > 0
    assert features.oi_delta_4h > 0
    assert features.taker_buy_sell_ratio > 1.0
    assert features.derivatives_score > 60.0


def test_derivatives_features_penalize_price_up_with_oi_down_and_hot_funding():
    frame = pd.DataFrame(
        [
            {"ts": "2026-01-01T00:00:00Z", "close": 100.0, "open_interest": 1000.0, "funding_rate": 0.0001, "quote_volume": 1000.0, "taker_buy_quote": 900.0},
            {"ts": "2026-01-01T01:00:00Z", "close": 105.0, "open_interest": 900.0, "funding_rate": 0.0002, "quote_volume": 1000.0, "taker_buy_quote": 920.0},
            {"ts": "2026-01-01T04:00:00Z", "close": 112.0, "open_interest": 800.0, "funding_rate": 0.0020, "quote_volume": 1000.0, "taker_buy_quote": 930.0},
        ]
    )

    features = compute_derivatives_features(frame)

    assert features.oi_delta_1h < 0
    assert features.oi_delta_4h < 0
    assert features.funding_zscore > 1.0
    assert features.derivatives_score < 45.0
```

- [ ] **Step 2: Run derivatives tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_derivatives.py -q
```

Expected: FAIL because `compute_derivatives_features` does not exist.

- [ ] **Step 3: Implement derivatives features**

Replace `src/altcoin_trend/features/derivatives.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


def clamp_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


@dataclass(frozen=True)
class DerivativesFeature:
    oi_delta_1h: float | None
    oi_delta_4h: float | None
    funding_zscore: float | None
    taker_buy_sell_ratio: float | None
    derivatives_score: float


def _latest_non_null(ordered: pd.DataFrame, column: str) -> tuple[pd.Timestamp, float] | None:
    if column not in ordered.columns:
        return None
    values = ordered[["ts", column]].dropna()
    if values.empty:
        return None
    latest = values.iloc[-1]
    return latest["ts"], float(latest[column])


def _value_at_or_before(ordered: pd.DataFrame, column: str, ts: pd.Timestamp) -> float | None:
    if column not in ordered.columns:
        return None
    values = ordered[ordered["ts"] <= ts][column].dropna()
    if values.empty:
        return None
    value = float(values.iloc[-1])
    return value if value > 0 else None


def _delta_pct(ordered: pd.DataFrame, column: str, hours: int) -> float | None:
    latest = _latest_non_null(ordered, column)
    if latest is None:
        return None
    latest_ts, latest_value = latest
    previous = _value_at_or_before(ordered, column, latest_ts - pd.Timedelta(hours=hours))
    if previous is None:
        return None
    return ((latest_value / previous) - 1.0) * 100.0


def _funding_zscore(ordered: pd.DataFrame) -> float | None:
    if "funding_rate" not in ordered.columns:
        return None
    values = ordered["funding_rate"].dropna().astype(float)
    if len(values) < 2:
        return None
    std = float(values.std(ddof=0))
    if std == 0:
        return 0.0
    return (float(values.iloc[-1]) - float(values.mean())) / std


def _taker_buy_sell_ratio(ordered: pd.DataFrame) -> float | None:
    if "taker_buy_quote" not in ordered.columns or "quote_volume" not in ordered.columns:
        return None
    latest = ordered.dropna(subset=["taker_buy_quote", "quote_volume"])
    if latest.empty:
        return None
    row = latest.iloc[-1]
    buy_quote = float(row["taker_buy_quote"])
    total_quote = float(row["quote_volume"])
    sell_quote = total_quote - buy_quote
    if sell_quote <= 0:
        return None
    return buy_quote / sell_quote


def compute_derivatives_features(frame: pd.DataFrame) -> DerivativesFeature:
    if frame.empty:
        return DerivativesFeature(None, None, None, None, 50.0)

    ordered = frame.copy()
    ordered["ts"] = pd.to_datetime(ordered["ts"], utc=True)
    ordered = ordered.sort_values("ts")
    oi_delta_1h = _delta_pct(ordered, "open_interest", 1)
    oi_delta_4h = _delta_pct(ordered, "open_interest", 4)
    funding_zscore = _funding_zscore(ordered)
    taker_ratio = _taker_buy_sell_ratio(ordered)

    first_close = float(ordered["close"].iloc[0]) if "close" in ordered.columns else 0.0
    latest_close = float(ordered["close"].iloc[-1]) if "close" in ordered.columns else 0.0
    price_return = ((latest_close / first_close) - 1.0) * 100.0 if first_close > 0 else 0.0

    score = 50.0
    for delta in (oi_delta_1h, oi_delta_4h):
        if delta is None:
            continue
        if price_return > 0 and delta > 0:
            score += min(12.0, delta * 0.8)
        elif price_return > 0 and delta < 0:
            score += max(-15.0, delta * 0.9)
        elif price_return < 0 and delta > 0:
            score -= min(10.0, delta * 0.5)

    if funding_zscore is not None and funding_zscore > 1.0:
        score -= min(18.0, (funding_zscore - 1.0) * 8.0)
    elif funding_zscore is not None and funding_zscore < -1.0 and price_return > 0:
        score += min(6.0, abs(funding_zscore + 1.0) * 3.0)

    if taker_ratio is not None:
        if 1.05 <= taker_ratio <= 1.8:
            score += 5.0
        elif taker_ratio > 3.0:
            score -= 8.0

    return DerivativesFeature(
        oi_delta_1h=oi_delta_1h,
        oi_delta_4h=oi_delta_4h,
        funding_zscore=funding_zscore,
        taker_buy_sell_ratio=taker_ratio,
        derivatives_score=clamp_score(score),
    )
```

- [ ] **Step 4: Run derivatives tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/test_derivatives.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

Run:

```bash
git add tests/test_derivatives.py src/altcoin_trend/features/derivatives.py
git commit -m "feat: score derivatives confirmation"
```

Expected: commit succeeds.

## Task 6: Wire Derivatives Features Into Snapshots and Explain

**Files:**
- Modify: `tests/test_scheduler.py`
- Modify: `tests/test_scoring.py`
- Modify: `src/altcoin_trend/scheduler.py`
- Modify: `src/altcoin_trend/signals/explain.py`

- [ ] **Step 1: Write failing scheduler integration test**

Append to `tests/test_scheduler.py`:

```python
def test_build_snapshot_rows_uses_data_driven_derivatives_score():
    snapshot_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    market_rows = pd.DataFrame(
        [
            {
                "asset_id": 50,
                "exchange": "binance",
                "symbol": "SOLUSDT",
                "base_asset": "SOL",
                "ts": pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=hour),
                "open": 100.0 + hour,
                "high": 101.0 + hour,
                "low": 99.0 + hour,
                "close": 100.0 + hour * 3,
                "volume": 10.0,
                "quote_volume": 1000.0,
                "taker_buy_quote": 560.0,
                "open_interest": 1000.0 + hour * 100.0,
                "funding_rate": 0.0001,
            }
            for hour in range(5)
        ]
    )

    feature_rows, _ = build_snapshot_rows(market_rows, snapshot_ts)
    row = feature_rows[0]

    assert row["oi_delta_1h"] > 0
    assert row["oi_delta_4h"] > 0
    assert row["taker_buy_sell_ratio"] > 1.0
    assert row["derivatives_score"] > 50.0
```

- [ ] **Step 2: Write failing explain test**

Append to `tests/test_scoring.py`:

```python
def test_build_explain_text_includes_derivatives_values_and_missing_as_na():
    text = build_explain_text(
        {
            "exchange": "binance",
            "symbol": "SOLUSDT",
            "final_score": 88.4,
            "tier": "strong",
            "trend_score": 92.0,
            "volume_breakout_score": 81.0,
            "relative_strength_score": 77.5,
            "derivatives_score": 64.0,
            "quality_score": 90.0,
            "oi_delta_1h": 4.321,
            "oi_delta_4h": None,
            "funding_zscore": 1.25,
            "taker_buy_sell_ratio": 1.4,
            "veto_reason_codes": [],
        }
    )

    assert "Derivatives:" in text
    assert "OI delta 1h: 4.32" in text
    assert "OI delta 4h: n/a" in text
    assert "Funding z-score: 1.25" in text
    assert "Taker buy/sell ratio: 1.40" in text
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_scheduler.py::test_build_snapshot_rows_uses_data_driven_derivatives_score tests/test_scoring.py::test_build_explain_text_includes_derivatives_values_and_missing_as_na -q
```

Expected: FAIL because scheduler still uses fixed derivatives score and explain does not render fields.

- [ ] **Step 4: Wire scheduler**

In `src/altcoin_trend/scheduler.py`, import:

```python
from altcoin_trend.features.derivatives import DerivativesFeature, compute_derivatives_features
```

Change `_component_scores` signature:

```python
    derivatives_score: float = 50.0,
```

Change score entry:

```python
        "derivatives_score": max(0.0, min(100.0, float(derivatives_score))),
```

Inside `build_snapshot_rows`, after `relative_strength`, add:

```python
        derivatives = compute_derivatives_features(group)
```

Change `_component_scores` call:

```python
        scores = _component_scores(
            group,
            timeframe_features,
            relative_strength.relative_strength_score,
            derivatives.derivatives_score,
        )
```

Add feature row fields:

```python
                "oi_delta_1h": derivatives.oi_delta_1h,
                "oi_delta_4h": derivatives.oi_delta_4h,
                "funding_zscore": derivatives.funding_zscore,
                "taker_buy_sell_ratio": derivatives.taker_buy_sell_ratio,
```

In `load_explain_row`, select:

```sql
            fs.oi_delta_1h,
            fs.oi_delta_4h,
            fs.funding_zscore,
            fs.taker_buy_sell_ratio,
```

- [ ] **Step 5: Wire explain**

In `src/altcoin_trend/signals/explain.py`, add lines after the RS detail block:

```python
        "Derivatives:",
        f"OI delta 1h: {_format_optional_float(_get(row, 'oi_delta_1h', None))}",
        f"OI delta 4h: {_format_optional_float(_get(row, 'oi_delta_4h', None))}",
        f"Funding z-score: {_format_optional_float(_get(row, 'funding_zscore', None))}",
        f"Taker buy/sell ratio: {_format_optional_float(_get(row, 'taker_buy_sell_ratio', None))}",
```

- [ ] **Step 6: Run scheduler/explain tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/test_scheduler.py::test_build_snapshot_rows_uses_data_driven_derivatives_score tests/test_scoring.py::test_build_explain_text_includes_derivatives_values_and_missing_as_na -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

Run:

```bash
git add tests/test_scheduler.py tests/test_scoring.py src/altcoin_trend/scheduler.py src/altcoin_trend/signals/explain.py
git commit -m "feat: include derivatives confirmation in snapshots"
```

Expected: commit succeeds.

## Task 7: Derivatives Backfill CLI

**Files:**
- Create: `src/altcoin_trend/ingest/derivatives.py`
- Modify: `tests/test_cli.py`
- Modify: `src/altcoin_trend/cli.py`

- [ ] **Step 1: Write failing CLI test**

Append to `tests/test_cli.py`:

```python
def test_cli_bootstrap_derivatives_uses_loaded_settings(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "altcoin_trend.cli.load_settings",
        lambda: AppSettings(default_exchanges="binance,bybit", quote_asset="USDT"),
    )
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.bootstrap_derivatives",
        lambda adapter, engine, settings, lookback_days, now: calls.append((adapter.exchange, lookback_days)) or 7,
    )

    result = CliRunner().invoke(app, ["bootstrap-derivatives", "--lookback-days", "31"])

    assert result.exit_code == 0
    assert calls == [("binance", 31), ("bybit", 31)]
    assert "Derivatives bootstrap binance updates=7" in result.output
    assert "Derivatives bootstrap bybit updates=7" in result.output
```

- [ ] **Step 2: Run CLI test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py::test_cli_bootstrap_derivatives_uses_loaded_settings -q
```

Expected: FAIL because command and function do not exist.

- [ ] **Step 3: Create derivatives ingest module**

Create `src/altcoin_trend/ingest/derivatives.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from altcoin_trend.config import AppSettings
from altcoin_trend.ingest.bootstrap import filter_instruments
from altcoin_trend.models import FundingRateObservation, LongShortRatioObservation, OpenInterestObservation


def _to_epoch_ms(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def _update_market_1m_derivative(connection, asset_id: int, ts: datetime, values: dict[str, float]) -> int:
    if not values:
        return 0
    assignments = ", ".join(f"{key} = :{key}" for key in values)
    statement = text(
        f"""
        UPDATE alt_core.market_1m
        SET {assignments}
        WHERE asset_id = :asset_id
          AND ts = :ts
        """
    )
    result = connection.execute(statement, {"asset_id": asset_id, "ts": ts, **values})
    rowcount = getattr(result, "rowcount", 0)
    return int(rowcount) if rowcount is not None and rowcount > 0 else 0


def _asset_ids_for_exchange(engine, exchange: str) -> dict[str, int]:
    statement = text(
        """
        SELECT symbol, asset_id
        FROM alt_core.asset_master
        WHERE exchange = :exchange
          AND market_type = 'usdt_perp'
        """
    )
    with engine.begin() as connection:
        return {row["symbol"]: int(row["asset_id"]) for row in connection.execute(statement, {"exchange": exchange}).mappings()}


def bootstrap_derivatives(adapter, engine, settings: AppSettings, lookback_days: int, now: datetime) -> int:
    instruments = filter_instruments(adapter.fetch_instruments(), settings=settings, now=now)
    asset_ids = _asset_ids_for_exchange(engine, adapter.exchange)
    start_ms = _to_epoch_ms(now) - lookback_days * 86_400_000
    end_ms = _to_epoch_ms(now)
    updates = 0

    with engine.begin() as connection:
        for instrument in instruments:
            asset_id = asset_ids.get(instrument.symbol)
            if asset_id is None:
                continue
            if hasattr(adapter, "fetch_open_interest_history"):
                for observation in adapter.fetch_open_interest_history(instrument.symbol, start_ms, end_ms, "1h"):
                    updates += _update_market_1m_derivative(connection, asset_id, observation.ts, {"open_interest": observation.open_interest})
            if hasattr(adapter, "fetch_funding_rate_history"):
                for observation in adapter.fetch_funding_rate_history(instrument.symbol, start_ms, end_ms):
                    updates += _update_market_1m_derivative(connection, asset_id, observation.ts, {"funding_rate": observation.funding_rate})
            if hasattr(adapter, "fetch_long_short_ratio_history"):
                for observation in adapter.fetch_long_short_ratio_history(instrument.symbol, start_ms, end_ms, "1h"):
                    updates += _update_market_1m_derivative(connection, asset_id, observation.ts, {"long_short_ratio": observation.long_short_ratio})
    return updates
```

- [ ] **Step 4: Add CLI command**

In `src/altcoin_trend/cli.py`, import:

```python
from altcoin_trend.ingest.derivatives import bootstrap_derivatives
```

Add command after `bootstrap`:

```python
@app.command("bootstrap-derivatives")
def bootstrap_derivatives_command(lookback_days: int = typer.Option(31, "--lookback-days", min=1)) -> None:
    settings = load_settings()
    engine = build_engine(settings)
    now = datetime.now(timezone.utc)
    for exchange in settings.exchanges:
        if exchange == "binance":
            adapter = BinancePublicAdapter()
        elif exchange == "bybit":
            adapter = BybitPublicAdapter()
        else:
            raise typer.BadParameter(f"Unsupported exchange: {exchange}")
        updates = bootstrap_derivatives(adapter=adapter, engine=engine, settings=settings, lookback_days=lookback_days, now=now)
        typer.echo(f"Derivatives bootstrap {exchange} updates={updates}")
```

- [ ] **Step 5: Run CLI test to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py::test_cli_bootstrap_derivatives_uses_loaded_settings -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 6**

Run:

```bash
git add tests/test_cli.py src/altcoin_trend/cli.py src/altcoin_trend/ingest/derivatives.py
git commit -m "feat: add derivatives bootstrap command"
```

Expected: commit succeeds.

## Task 8: Full Verification and Operational Backfill

**Files:**
- Verify and operate only.

- [ ] **Step 1: Run full test suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Smoke-test CLI help**

Run:

```bash
.venv/bin/acts --help
```

Expected: command lists `bootstrap-derivatives`.

- [ ] **Step 3: Configure conservative allowlist for operational run**

Use environment variables for the command instead of editing config:

```bash
ACTS_SYMBOL_ALLOWLIST=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,LINKUSDT,AVAXUSDT,ARBUSDT,OPUSDT .venv/bin/acts bootstrap --lookback-days 31
```

Expected: command completes and prints one line per configured exchange.

- [ ] **Step 4: Backfill derivatives**

Run:

```bash
ACTS_SYMBOL_ALLOWLIST=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,LINKUSDT,AVAXUSDT,ARBUSDT,OPUSDT .venv/bin/acts bootstrap-derivatives --lookback-days 31
```

Expected: command completes and prints update counts. If an exchange blocks a derivatives endpoint, record the error and continue with available market data if possible.

- [ ] **Step 5: Generate snapshots**

Run:

```bash
.venv/bin/acts run-once
```

Expected: healthy result with nonzero feature and rank writes.

- [ ] **Step 6: Inspect database coverage**

Run:

```bash
.venv/bin/python - <<'PY'
from sqlalchemy import text
from altcoin_trend.config import load_settings
from altcoin_trend.db import build_engine
engine = build_engine(load_settings())
with engine.begin() as c:
    for name, q in {
        "assets": "select count(*) from alt_core.asset_master",
        "bars": "select count(*), min(ts), max(ts), count(distinct asset_id) from alt_core.market_1m",
        "derivatives": "select count(*) filter (where open_interest is not null), count(*) filter (where funding_rate is not null), count(*) filter (where long_short_ratio is not null) from alt_core.market_1m",
        "features": "select count(*), max(ts) from alt_signal.feature_snapshot",
        "ranks": "select count(*), max(ts) from alt_signal.rank_snapshot",
    }.items():
        print(name, c.execute(text(q)).first())
PY
```

Expected: nonzero bars, features, and ranks.

- [ ] **Step 7: Inspect rank and explain output**

Run:

```bash
.venv/bin/acts rank --limit 10
.venv/bin/acts explain SOLUSDT --exchange binance
```

Expected: rank output lists symbols and explain output includes relative strength and derivatives sections.

- [ ] **Step 8: Inspect git status**

Run:

```bash
git status --short
```

Expected: clean after commits.

- [ ] **Step 9: Report outcome**

Summarize:

- full test result,
- bootstrap instrument and bar counts,
- derivatives update counts,
- database date range,
- top 10 rank output,
- one explain sample,
- any skipped endpoints or exchange errors.
