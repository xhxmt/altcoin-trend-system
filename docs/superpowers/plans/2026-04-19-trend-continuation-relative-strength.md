# Trend Continuation and Relative Strength Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the ranking pipeline produce more useful trend continuation and market leadership signals by replacing fixed relative strength with data-driven BTC/ETH and universe-relative scoring.

**Architecture:** Add deterministic relative strength helpers in `features/relative_strength.py`, then wire them into `scheduler.build_snapshot_rows`. Keep all computation local to existing market rows and existing snapshot tables. Extend explain output to show the RS values already present in `feature_snapshot`.

**Tech Stack:** Python 3.12, pandas, SQLAlchemy, Typer, pytest.

---

## File Structure

- Modify `src/altcoin_trend/features/relative_strength.py`
  - Owns return-window math, benchmark comparison, cross-sectional fallback, and 0-100 relative strength scoring.
- Modify `src/altcoin_trend/scheduler.py`
  - Loads enough recent market rows, computes RS features once per snapshot, passes per-asset RS into component scoring, and inserts RS columns.
- Modify `src/altcoin_trend/signals/explain.py`
  - Renders RS fields with `n/a` for missing values.
- Modify `tests/test_scoring.py`
  - Adds explain-output coverage for RS fields.
- Modify `tests/test_scheduler.py`
  - Adds snapshot integration coverage for non-fixed RS scoring and RS fields.
- Create `tests/test_relative_strength.py`
  - Covers relative strength math and fallback behavior.

## Task 1: Relative Strength Pure Functions

**Files:**
- Create: `tests/test_relative_strength.py`
- Modify: `src/altcoin_trend/features/relative_strength.py`

- [ ] **Step 1: Write failing tests for benchmark and fallback scoring**

Create `tests/test_relative_strength.py`:

```python
import pandas as pd

from altcoin_trend.features.relative_strength import build_relative_strength_features


def _rows(asset_id: int, symbol: str, closes: tuple[float, float, float]):
    points = (
        ("2026-01-01T00:00:00Z", closes[0]),
        ("2026-01-24T00:00:00Z", closes[1]),
        ("2026-01-31T00:00:00Z", closes[2]),
    )
    return [
        {
            "asset_id": asset_id,
            "exchange": "binance",
            "symbol": symbol,
            "base_asset": symbol.removesuffix("USDT"),
            "ts": ts,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1.0,
            "quote_volume": 1.0,
        }
        for ts, close in points
    ]


def test_relative_strength_features_compare_asset_returns_to_btc_and_eth():
    frame = pd.DataFrame(
        _rows(1, "BTCUSDT", (100.0, 100.0, 105.0))
        + _rows(2, "ETHUSDT", (100.0, 100.0, 110.0))
        + _rows(3, "SOLUSDT", (100.0, 100.0, 120.0))
    )

    features = build_relative_strength_features(frame)

    sol = features[3]
    assert sol.rs_btc_7d == 15.0
    assert sol.rs_eth_7d == 10.0
    assert sol.rs_btc_30d == 15.0
    assert sol.rs_eth_30d == 10.0
    assert sol.relative_strength_score > 80.0


def test_relative_strength_score_penalizes_underperformance_against_benchmarks():
    frame = pd.DataFrame(
        _rows(1, "BTCUSDT", (100.0, 100.0, 110.0))
        + _rows(2, "ETHUSDT", (100.0, 100.0, 120.0))
        + _rows(3, "LAGUSDT", (100.0, 100.0, 95.0))
    )

    features = build_relative_strength_features(frame)

    laggard = features[3]
    assert laggard.rs_btc_7d == -15.0
    assert laggard.rs_eth_7d == -25.0
    assert laggard.relative_strength_score < 30.0


def test_relative_strength_uses_cross_sectional_fallback_without_benchmarks():
    frame = pd.DataFrame(
        _rows(10, "LEADERUSDT", (100.0, 100.0, 130.0))
        + _rows(11, "MIDUSDT", (100.0, 100.0, 110.0))
        + _rows(12, "WEAKUSDT", (100.0, 100.0, 90.0))
    )

    features = build_relative_strength_features(frame)

    assert features[10].relative_strength_score > features[11].relative_strength_score
    assert features[11].relative_strength_score > features[12].relative_strength_score
    assert features[10].rs_btc_7d is None
    assert features[10].rs_eth_30d is None
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_relative_strength.py -q
```

Expected: FAIL because `build_relative_strength_features` is not defined.

- [ ] **Step 3: Implement minimal relative strength helpers**

Replace `src/altcoin_trend/features/relative_strength.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pandas as pd


def clamp_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


@dataclass(frozen=True)
class RelativeStrengthFeature:
    return_7d: float | None
    return_30d: float | None
    rs_btc_7d: float | None
    rs_eth_7d: float | None
    rs_btc_30d: float | None
    rs_eth_30d: float | None
    relative_strength_score: float


def _return_pct(group: pd.DataFrame, days: int) -> float | None:
    if group.empty:
        return None
    ordered = group.sort_values("ts")
    latest = ordered.iloc[-1]
    latest_close = float(latest["close"])
    anchor_ts = latest["ts"] - pd.Timedelta(days=days)
    history = ordered[ordered["ts"] <= anchor_ts]
    if history.empty:
        return None
    anchor_close = float(history.iloc[-1]["close"])
    if anchor_close <= 0:
        return None
    return round(((latest_close / anchor_close) - 1.0) * 100.0, 6)


def _score_from_edges(edges: list[float]) -> float:
    if not edges:
        return 50.0
    weighted_edge = sum(edges) / len(edges)
    return clamp_score(50.0 + weighted_edge * 2.0)


def _fallback_score(return_7d: float | None, return_30d: float | None, median_7d: float | None, median_30d: float | None) -> float:
    edges: list[float] = []
    if return_7d is not None and median_7d is not None:
        edges.extend([return_7d - median_7d, return_7d - median_7d])
    if return_30d is not None and median_30d is not None:
        edges.append(return_30d - median_30d)
    return _score_from_edges(edges)


def _benchmark_score(
    return_7d: float | None,
    return_30d: float | None,
    btc_7d: float | None,
    eth_7d: float | None,
    btc_30d: float | None,
    eth_30d: float | None,
) -> tuple[float | None, float | None, float | None, float | None, float]:
    rs_btc_7d = return_7d - btc_7d if return_7d is not None and btc_7d is not None else None
    rs_eth_7d = return_7d - eth_7d if return_7d is not None and eth_7d is not None else None
    rs_btc_30d = return_30d - btc_30d if return_30d is not None and btc_30d is not None else None
    rs_eth_30d = return_30d - eth_30d if return_30d is not None and eth_30d is not None else None
    edges: list[float] = []
    for value in (rs_btc_7d, rs_eth_7d):
        if value is not None:
            edges.extend([value, value])
    for value in (rs_btc_30d, rs_eth_30d):
        if value is not None:
            edges.append(value)
    return rs_btc_7d, rs_eth_7d, rs_btc_30d, rs_eth_30d, _score_from_edges(edges)


def build_relative_strength_features(frame: pd.DataFrame) -> dict[int, RelativeStrengthFeature]:
    if frame.empty:
        return {}

    working = frame.copy()
    working["ts"] = pd.to_datetime(working["ts"], utc=True)
    returns: dict[int, dict[str, Any]] = {}
    for asset_id, group in working.groupby("asset_id"):
        latest = group.sort_values("ts").iloc[-1]
        returns[int(asset_id)] = {
            "exchange": str(latest["exchange"]),
            "symbol": str(latest["symbol"]).upper(),
            "return_7d": _return_pct(group, 7),
            "return_30d": _return_pct(group, 30),
        }

    result: dict[int, RelativeStrengthFeature] = {}
    for exchange, exchange_rows in pd.DataFrame.from_dict(returns, orient="index").groupby("exchange"):
        benchmark_by_symbol = {str(row["symbol"]): row for row in exchange_rows.to_dict("records")}
        btc = benchmark_by_symbol.get("BTCUSDT", {})
        eth = benchmark_by_symbol.get("ETHUSDT", {})
        median_7d = exchange_rows["return_7d"].dropna().median()
        median_30d = exchange_rows["return_30d"].dropna().median()
        median_7d_value = float(median_7d) if pd.notna(median_7d) else None
        median_30d_value = float(median_30d) if pd.notna(median_30d) else None

        for raw_asset_id, row in exchange_rows.iterrows():
            asset_id = int(raw_asset_id)
            return_7d = row["return_7d"] if pd.notna(row["return_7d"]) else None
            return_30d = row["return_30d"] if pd.notna(row["return_30d"]) else None
            btc_7d = btc.get("return_7d") if btc else None
            eth_7d = eth.get("return_7d") if eth else None
            btc_30d = btc.get("return_30d") if btc else None
            eth_30d = eth.get("return_30d") if eth else None
            if any(value is not None and pd.notna(value) for value in (btc_7d, eth_7d, btc_30d, eth_30d)):
                rs_btc_7d, rs_eth_7d, rs_btc_30d, rs_eth_30d, score = _benchmark_score(
                    return_7d,
                    return_30d,
                    btc_7d if btc_7d is not None and pd.notna(btc_7d) else None,
                    eth_7d if eth_7d is not None and pd.notna(eth_7d) else None,
                    btc_30d if btc_30d is not None and pd.notna(btc_30d) else None,
                    eth_30d if eth_30d is not None and pd.notna(eth_30d) else None,
                )
            else:
                rs_btc_7d = rs_eth_7d = rs_btc_30d = rs_eth_30d = None
                score = _fallback_score(return_7d, return_30d, median_7d_value, median_30d_value)

            result[asset_id] = RelativeStrengthFeature(
                return_7d=return_7d,
                return_30d=return_30d,
                rs_btc_7d=rs_btc_7d,
                rs_eth_7d=rs_eth_7d,
                rs_btc_30d=rs_btc_30d,
                rs_eth_30d=rs_eth_30d,
                relative_strength_score=score,
            )

    return result
```

- [ ] **Step 4: Run relative strength tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/test_relative_strength.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

Run:

```bash
git add tests/test_relative_strength.py src/altcoin_trend/features/relative_strength.py
git commit -m "feat: compute relative strength features"
```

Expected: commit succeeds.

## Task 2: Wire Relative Strength Into Snapshot Rows

**Files:**
- Modify: `tests/test_scheduler.py`
- Modify: `src/altcoin_trend/scheduler.py`

- [ ] **Step 1: Write failing snapshot integration test**

Append this test to `tests/test_scheduler.py`:

```python
def _relative_strength_rows(asset_id: int, symbol: str, closes: tuple[float, float, float]):
    points = (
        ("2026-01-01T00:00:00Z", closes[0]),
        ("2026-01-24T00:00:00Z", closes[1]),
        ("2026-01-31T00:00:00Z", closes[2]),
    )
    return [
        {
            "asset_id": asset_id,
            "exchange": "binance",
            "symbol": symbol,
            "base_asset": symbol.removesuffix("USDT"),
            "ts": ts,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 10.0,
            "quote_volume": 1000.0,
        }
        for ts, close in points
    ]


def test_build_snapshot_rows_uses_data_driven_relative_strength():
    snapshot_ts = datetime(2026, 1, 31, tzinfo=timezone.utc)
    market_rows = pd.DataFrame(
        _relative_strength_rows(1, "BTCUSDT", (100.0, 100.0, 105.0))
        + _relative_strength_rows(2, "ETHUSDT", (100.0, 100.0, 110.0))
        + _relative_strength_rows(3, "SOLUSDT", (100.0, 100.0, 120.0))
        + _relative_strength_rows(4, "LAGUSDT", (100.0, 100.0, 95.0))
    )

    feature_rows, rank_rows = build_snapshot_rows(market_rows, snapshot_ts)
    by_symbol = {row["symbol"]: row for row in feature_rows}

    assert by_symbol["SOLUSDT"]["rs_btc_7d"] == 15.0
    assert by_symbol["SOLUSDT"]["rs_eth_7d"] == 10.0
    assert by_symbol["SOLUSDT"]["rs_btc_30d"] == 15.0
    assert by_symbol["SOLUSDT"]["rs_eth_30d"] == 10.0
    assert by_symbol["SOLUSDT"]["relative_strength_score"] > 80.0
    assert by_symbol["LAGUSDT"]["relative_strength_score"] < 30.0
    assert by_symbol["SOLUSDT"]["final_score"] > by_symbol["LAGUSDT"]["final_score"]
    assert rank_rows[0]["symbol"] == "SOLUSDT"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_scheduler.py::test_build_snapshot_rows_uses_data_driven_relative_strength -q
```

Expected: FAIL because snapshot rows do not include the RS values and still use fixed `relative_strength_score`.

- [ ] **Step 3: Import and compute relative strength features in scheduler**

In `src/altcoin_trend/scheduler.py`, add this import:

```python
from altcoin_trend.features.relative_strength import RelativeStrengthFeature, build_relative_strength_features
```

In `build_snapshot_rows`, compute the feature map before the `for asset_id, group ...` loop:

```python
    relative_strength_by_asset = build_relative_strength_features(working)
```

Inside the loop, before `_component_scores`, add:

```python
        relative_strength = relative_strength_by_asset.get(
            int(asset_id),
            RelativeStrengthFeature(
                return_7d=None,
                return_30d=None,
                rs_btc_7d=None,
                rs_eth_7d=None,
                rs_btc_30d=None,
                rs_eth_30d=None,
                relative_strength_score=50.0,
            ),
        )
```

Change the `_component_scores` call from:

```python
        scores = _component_scores(group, timeframe_features)
```

to:

```python
        scores = _component_scores(group, timeframe_features, relative_strength.relative_strength_score)
```

Add these keys to the `feature_rows.append` dictionary:

```python
                "rs_btc_7d": relative_strength.rs_btc_7d,
                "rs_eth_7d": relative_strength.rs_eth_7d,
                "rs_btc_30d": relative_strength.rs_btc_30d,
                "rs_eth_30d": relative_strength.rs_eth_30d,
```

- [ ] **Step 4: Update `_component_scores` signature and relative strength score**

Change the function signature in `src/altcoin_trend/scheduler.py`:

```python
def _component_scores(
    group: pd.DataFrame,
    timeframe_features: dict[str, Any] | None = None,
    relative_strength_score: float = 50.0,
) -> dict[str, float]:
```

Change this score entry:

```python
        "relative_strength_score": 50.0,
```

to:

```python
        "relative_strength_score": max(0.0, min(100.0, float(relative_strength_score))),
```

- [ ] **Step 5: Insert RS columns into feature snapshots**

In `write_run_once_snapshots`, the existing `feature_insert_rows` comprehension keeps keys except `base_asset`, `tier`, and `primary_reason`. No change is needed there after Task 2 Step 3 because the SQL table already has the RS columns.

Run this command to confirm the table has the columns:

```bash
grep -n "rs_btc_7d\\|rs_eth_7d\\|rs_btc_30d\\|rs_eth_30d" sql/003_signal_schema.sql src/altcoin_trend/migrations/003_signal_schema.sql
```

Expected: each RS column appears in both SQL files.

- [ ] **Step 6: Run scheduler integration test to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/test_scheduler.py::test_build_snapshot_rows_uses_data_driven_relative_strength -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

Run:

```bash
git add tests/test_scheduler.py src/altcoin_trend/scheduler.py
git commit -m "feat: include relative strength in snapshots"
```

Expected: commit succeeds.

## Task 3: Improve Trend Continuation Scoring

**Files:**
- Modify: `tests/test_scheduler.py`
- Modify: `src/altcoin_trend/scheduler.py`

- [ ] **Step 1: Write failing extension-risk test**

Append this test to `tests/test_scheduler.py`:

```python
def test_build_snapshot_rows_penalizes_extreme_extension_above_4h_ema():
    snapshot_ts = datetime(2026, 2, 1, tzinfo=timezone.utc)
    rows = []
    for minute in range(31 * 24 * 60):
        ts = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=minute)
        steady_close = 100.0 + 0.01 * minute
        extended_close = 100.0 + 0.01 * minute
        if minute > 31 * 24 * 60 - 240:
            extended_close += 90.0
        for asset_id, symbol, close in (
            (20, "STEADYUSDT", steady_close),
            (21, "EXTENDEDUSDT", extended_close),
        ):
            rows.append(
                {
                    "asset_id": asset_id,
                    "exchange": "binance",
                    "symbol": symbol,
                    "base_asset": symbol.removesuffix("USDT"),
                    "ts": ts,
                    "open": close,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 10.0,
                    "quote_volume": 1000.0,
                }
            )

    feature_rows, _ = build_snapshot_rows(pd.DataFrame(rows), snapshot_ts)
    by_symbol = {row["symbol"]: row for row in feature_rows}

    assert by_symbol["STEADYUSDT"]["trend_score"] > by_symbol["EXTENDEDUSDT"]["trend_score"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_scheduler.py::test_build_snapshot_rows_penalizes_extreme_extension_above_4h_ema -q
```

Expected: FAIL because extreme extension is not sufficiently penalized.

- [ ] **Step 3: Add return windows to timeframe features**

In `_higher_timeframe_features`, add defaults to the `features` dictionary:

```python
        "return_7d": None,
        "return_30d": None,
```

At the end of `_higher_timeframe_features`, after 1d feature computation, add:

```python
    if not working.empty:
        ordered = working.sort_values("ts")
        latest = ordered.iloc[-1]
        latest_close = float(latest["close"])
        for days, key in ((7, "return_7d"), (30, "return_30d")):
            anchor_ts = latest["ts"] - pd.Timedelta(days=days)
            history = ordered[ordered["ts"] <= anchor_ts]
            if history.empty:
                continue
            anchor_close = float(history.iloc[-1]["close"])
            if anchor_close > 0:
                features[key] = ((latest_close / anchor_close) - 1.0) * 100.0
```

- [ ] **Step 4: Apply bounded extension penalty in `_component_scores`**

In `_component_scores`, after the ADX scoring block and before the existing `trend_score += max(...)` return-percent line, add:

```python
    return_7d = timeframe_features.get("return_7d")
    return_30d = timeframe_features.get("return_30d")
    ema20_1d = timeframe_features.get("ema20_1d")
    ema60_1d = timeframe_features.get("ema60_1d")

    if ema20_1d is not None and ema60_1d is not None and float(ema20_1d) > float(ema60_1d):
        trend_score += 10.0
    if return_7d is not None:
        trend_score += max(0.0, min(8.0, float(return_7d) / 4.0))
    if return_30d is not None:
        trend_score += max(0.0, min(7.0, float(return_30d) / 8.0))
    if ema20_4h is not None and float(ema20_4h) > 0:
        extension_pct = ((latest_close / float(ema20_4h)) - 1.0) * 100.0
        if extension_pct > 18.0:
            trend_score -= min(25.0, (extension_pct - 18.0) * 1.2)
```

Keep the final clamp that already bounds `trend_score` to 0-100.

- [ ] **Step 5: Run trend extension test to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/test_scheduler.py::test_build_snapshot_rows_penalizes_extreme_extension_above_4h_ema -q
```

Expected: PASS.

- [ ] **Step 6: Run scheduler tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_scheduler.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

Run:

```bash
git add tests/test_scheduler.py src/altcoin_trend/scheduler.py
git commit -m "feat: improve trend continuation scoring"
```

Expected: commit succeeds.

## Task 4: Load Enough History for 30-Day Features

**Files:**
- Modify: `tests/test_scheduler.py`
- Modify: `src/altcoin_trend/scheduler.py`

- [ ] **Step 1: Write failing test for time-window SQL**

Append this test to `tests/test_scheduler.py`:

```python
def test_load_market_rows_queries_recent_31_day_window():
    captured = {}

    class Result:
        def mappings(self):
            return self

        def all(self):
            return []

    class Connection:
        def execute(self, statement, params):
            captured["sql"] = str(statement)
            captured["params"] = params
            return Result()

    class Begin:
        def __enter__(self):
            return Connection()

        def __exit__(self, exc_type, exc, tb):
            return False

    class Engine:
        def begin(self):
            return Begin()

    from altcoin_trend.scheduler import _load_market_rows

    frame = _load_market_rows(Engine(), lookback_days=31)

    assert frame.empty
    assert "MAX(ts)" in captured["sql"]
    assert "make_interval(days => :lookback_days)" in captured["sql"]
    assert captured["params"] == {"lookback_days": 31}
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_scheduler.py::test_load_market_rows_queries_recent_31_day_window -q
```

Expected: FAIL because `_load_market_rows` uses a row limit instead of a 31-day time window.

- [ ] **Step 3: Change `_load_market_rows` to use a time window**

Replace `_load_market_rows` in `src/altcoin_trend/scheduler.py` with:

```python
def _load_market_rows(engine: Engine, lookback_days: int = 31) -> pd.DataFrame:
    statement = text(
        """
        WITH latest AS (
            SELECT MAX(ts) AS max_ts
            FROM alt_core.market_1m
        )
        SELECT
            m.asset_id,
            m.exchange,
            m.symbol,
            a.base_asset,
            m.ts,
            m.open,
            m.high,
            m.low,
            m.close,
            m.volume,
            m.quote_volume,
            m.trade_count,
            m.taker_buy_base,
            m.taker_buy_quote
        FROM alt_core.market_1m AS m
        JOIN alt_core.asset_master AS a ON a.asset_id = m.asset_id
        CROSS JOIN latest
        WHERE latest.max_ts IS NOT NULL
          AND m.ts >= latest.max_ts - make_interval(days => :lookback_days)
        ORDER BY m.asset_id, m.ts
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, {"lookback_days": lookback_days})
        return pd.DataFrame(result.mappings().all())
```

- [ ] **Step 4: Run the SQL-window test to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/test_scheduler.py::test_load_market_rows_queries_recent_31_day_window -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

Run:

```bash
git add tests/test_scheduler.py src/altcoin_trend/scheduler.py
git commit -m "feat: load market rows by signal lookback window"
```

Expected: commit succeeds.

## Task 5: Explain Relative Strength Values

**Files:**
- Modify: `tests/test_scoring.py`
- Modify: `src/altcoin_trend/signals/explain.py`
- Modify: `src/altcoin_trend/scheduler.py`

- [ ] **Step 1: Write failing explain-output test**

Append this test to `tests/test_scoring.py`:

```python
def test_build_explain_text_includes_relative_strength_values_and_missing_as_na():
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
            "rs_btc_7d": 12.34567,
            "rs_eth_7d": None,
            "rs_btc_30d": -1.25,
            "rs_eth_30d": 4.0,
            "veto_reason_codes": [],
        }
    )

    assert "Relative strength:" in text
    assert "RS vs BTC 7d: 12.35" in text
    assert "RS vs ETH 7d: n/a" in text
    assert "RS vs BTC 30d: -1.25" in text
    assert "RS vs ETH 30d: 4.00" in text
```

- [ ] **Step 2: Run the explain test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_scoring.py::test_build_explain_text_includes_relative_strength_values_and_missing_as_na -q
```

Expected: FAIL because explain output does not include RS fields.

- [ ] **Step 3: Add RS formatting to explain output**

In `src/altcoin_trend/signals/explain.py`, add this helper above `build_explain_text`:

```python
def _format_optional_float(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"
```

Inside `build_explain_text`, after the `"Breakdown:"` score lines and before veto handling, add:

```python
        "Relative strength:",
        f"RS vs BTC 7d: {_format_optional_float(_get(row, 'rs_btc_7d', None))}",
        f"RS vs ETH 7d: {_format_optional_float(_get(row, 'rs_eth_7d', None))}",
        f"RS vs BTC 30d: {_format_optional_float(_get(row, 'rs_btc_30d', None))}",
        f"RS vs ETH 30d: {_format_optional_float(_get(row, 'rs_eth_30d', None))}",
```

- [ ] **Step 4: Load RS fields for CLI explain**

In `src/altcoin_trend/scheduler.py`, add these columns to the `SELECT` list in `load_explain_row` after `fs.quality_score`:

```sql
            fs.rs_btc_7d,
            fs.rs_eth_7d,
            fs.rs_btc_30d,
            fs.rs_eth_30d,
```

- [ ] **Step 5: Run explain tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/test_scoring.py::test_build_explain_text_includes_relative_strength_values_and_missing_as_na tests/test_cli.py::test_cli_explain_prints_snapshot_when_available -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 5**

Run:

```bash
git add tests/test_scoring.py src/altcoin_trend/signals/explain.py src/altcoin_trend/scheduler.py
git commit -m "feat: explain relative strength signals"
```

Expected: commit succeeds.

## Task 6: Full Verification

**Files:**
- Verify only.

- [ ] **Step 1: Run the full test suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Inspect git status**

Run:

```bash
git status --short
```

Expected: no unstaged implementation changes. If there are changes, either commit the intended changes or inspect and leave unrelated user changes alone.

- [ ] **Step 3: Smoke-test CLI help**

Run:

```bash
.venv/bin/acts --help
```

Expected: command exits 0 and lists `init-db`, `bootstrap`, `run-once`, `rank`, `alerts`, and `explain`.

- [ ] **Step 4: Report outcome**

Summarize:

- tests run and pass/fail counts
- commits created
- whether RS fields are now computed, stored, and explained
- any operational caveat, especially that useful 30-day RS requires at least 31 days of rows in `alt_core.market_1m`
