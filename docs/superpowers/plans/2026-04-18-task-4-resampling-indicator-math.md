# Resampling and Indicator Math Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 1-minute market bar resampling and a small indicator module for EMA, true range, ATR, and ADX.

**Architecture:** Keep resampling in a focused feature module that operates on pandas DataFrames and returns normalized bar tables. Keep indicator math in a separate module that works on DataFrames or Series and returns copies/Series without mutating caller input.

**Tech Stack:** Python 3.12, pandas, numpy, pytest.

---

### Task 1: Resample 1m market bars

**Files:**
- Create: `src/altcoin_trend/features/__init__.py`
- Create: `src/altcoin_trend/features/resample.py`
- Test: `tests/test_resample.py`

- [ ] **Step 1: Write the failing test**

```python
import pandas as pd

from altcoin_trend.features.resample import resample_market_1m


def test_resample_market_1m_aggregates_one_5m_bucket():
    frame = pd.DataFrame(
        [
            {"ts": "2024-03-01T00:00:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0, "quote_volume": 1000.0, "trade_count": 1},
            {"ts": "2024-03-01T00:01:00Z", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.0, "volume": 20.0, "quote_volume": 2000.0, "trade_count": 2},
            {"ts": "2024-03-01T00:02:00Z", "open": 101.0, "high": 103.0, "low": 100.5, "close": 102.0, "volume": 30.0, "quote_volume": 3000.0, "trade_count": 3},
            {"ts": "2024-03-01T00:03:00Z", "open": 102.0, "high": 104.0, "low": 101.0, "close": 103.0, "volume": 40.0, "quote_volume": 4000.0, "trade_count": 4},
            {"ts": "2024-03-01T00:04:00Z", "open": 103.0, "high": 105.0, "low": 102.0, "close": 104.5, "volume": 50.0, "quote_volume": 5000.0, "trade_count": 5},
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_resample.py::test_resample_market_1m_aggregates_one_5m_bucket -v`
Expected: FAIL because `resample_market_1m` is not implemented yet.

- [ ] **Step 3: Write minimal implementation**

```python
from __future__ import annotations

import pandas as pd


_TIMEFRAME_RULES = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1d"}


def resample_market_1m(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_resample.py::test_resample_market_1m_aggregates_one_5m_bucket -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/altcoin_trend/features/__init__.py src/altcoin_trend/features/resample.py tests/test_resample.py
git commit -m "feat: add resampling and indicator math"
```

### Task 2: Indicator math helpers

**Files:**
- Create: `src/altcoin_trend/features/indicators.py`
- Test: `tests/test_indicators.py`

- [ ] **Step 1: Write the failing tests**

```python
import pandas as pd

from altcoin_trend.features.indicators import add_ema, true_range, atr, adx


def test_add_ema_adds_output_and_increases_for_rising_close():
    frame = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
    result = add_ema(frame, "close", span=2, output="ema_2")

    assert "ema_2" in result
    assert result["ema_2"].iloc[0] < result["ema_2"].iloc[-1]


def test_true_range_uses_previous_close():
    frame = pd.DataFrame({"high": [11.0, 12.0], "low": [9.0, 8.0], "close": [10.0, 11.0]})
    assert list(true_range(frame)) == [2.0, 4.0]


def test_atr_uses_rolling_mean_with_min_periods_one():
    frame = pd.DataFrame({"high": [11.0, 12.0], "low": [9.0, 8.0], "close": [10.0, 11.0]})
    assert atr(frame, window=2).iloc[-1] == 3.0


def test_adx_returns_same_length_and_non_null_values():
    frame = pd.DataFrame({"high": [10.0, 11.0, 12.0], "low": [9.0, 9.5, 10.0], "close": [9.5, 10.5, 11.0]})
    result = adx(frame, window=3)

    assert len(result) == len(frame)
    assert result.notna().any()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indicators.py -v`
Expected: FAIL because indicator helpers are not implemented yet.

- [ ] **Step 3: Write minimal implementation**

```python
from __future__ import annotations

import pandas as pd


def add_ema(frame: pd.DataFrame, column: str, span: int, output: str) -> pd.DataFrame:
    ...


def true_range(frame: pd.DataFrame) -> pd.Series:
    ...


def atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    ...


def adx(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indicators.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/altcoin_trend/features/indicators.py tests/test_indicators.py
git commit -m "feat: add resampling and indicator math"
```
