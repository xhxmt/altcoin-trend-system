# Signal Validation Trust Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `scripts/validate_ultra_signal_production.py` a trustworthy v2 signal validator with explicit timestamp, entry, forward-label, coverage, artifact, and comparison semantics.

**Architecture:** Keep the current script as the entrypoint and add focused internal helpers rather than creating a new CLI or package. Implement the v1.1 semantics contract in small layers: family registry, time/window helpers, validation path labels, coverage-aware summaries, artifact metadata, CLI flags, and comparison policy. Tests drive every behavior before implementation.

**Tech Stack:** Python 3.12, pandas, SQLAlchemy, argparse, pytest, existing `altcoin_trend.trade_backtest._prepare_feature_frame`.

---

## Scope Check

The spec is focused on one subsystem: validation trust for existing v2 signal evaluation. It does not require separate project plans for data backfill quality, deployment, Telegram alerts, or formal production observability.

## File Structure

- Modify: `scripts/validate_ultra_signal_production.py`
  - Owns the validator entrypoint, family registry, hourly aggregation query, forward-row query, path-label semantics, summaries, artifact writing, metadata, and comparison policy.
  - Keep functions small enough to test directly from pytest.
- Modify: `tests/test_validate_ultra_signal_production.py`
  - Keep existing artifact and ultra gate-flow tests.
  - Update tests that assert old metadata and metric names.
- Create: `tests/test_validate_signal_semantics.py`
  - Focused tests for v1.1 semantics: minute-open boundaries, signal availability, same-bar ambiguity, coverage denominators, sensitivity matrix, selector registry, default window, and comparison policy.
- Conditional modify: `docs/strategy/current-strategy.md`
  - Only if implementation includes a threshold change. This plan does not require threshold changes.

Do not modify unrelated dirty files. Each commit must include only the files listed in that task.

---

### Task 1: Add Family Registry And Selector Semantics

**Files:**
- Modify: `scripts/validate_ultra_signal_production.py`
- Create: `tests/test_validate_signal_semantics.py`

- [ ] **Step 1: Write failing selector registry tests**

Create `tests/test_validate_signal_semantics.py` with this import harness and selector tests:

```python
import importlib.util
from pathlib import Path

import pandas as pd
import pytest


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_ultra_signal_production.py"
_SPEC = importlib.util.spec_from_file_location("validate_ultra_signal_production", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_parse_signal_selector_normalizes_family_and_grade():
    family, grade, selector = _MODULE.parse_signal_selector("ignition_A")

    assert family.name == "ignition"
    assert family.selector_column == "ignition_grade"
    assert grade == "A"
    assert selector == "ignition_A"


def test_parse_signal_selector_rejects_unsupported_grade():
    with pytest.raises(ValueError, match="unsupported grade selector"):
        _MODULE.parse_signal_selector("ultra_high_conviction_A")


def test_select_signal_rows_uses_registry_columns():
    frame = pd.DataFrame(
        [
            {"symbol": "AUSDT", "ignition_grade": "A", "reacceleration_grade": None, "ultra_high_conviction": False},
            {"symbol": "BUSDT", "ignition_grade": "B", "reacceleration_grade": "B", "ultra_high_conviction": False},
            {"symbol": "CUSDT", "ignition_grade": None, "reacceleration_grade": None, "ultra_high_conviction": True},
        ]
    )

    assert _MODULE._select_signal_rows(frame, "ignition")["symbol"].tolist() == ["AUSDT", "BUSDT"]
    assert _MODULE._select_signal_rows(frame, "ignition_A")["symbol"].tolist() == ["AUSDT"]
    assert _MODULE._select_signal_rows(frame, "reacceleration_B")["symbol"].tolist() == ["BUSDT"]
    assert _MODULE._select_signal_rows(frame, "ultra_high_conviction")["symbol"].tolist() == ["CUSDT"]


def test_select_signal_rows_missing_selector_column_is_hard_error():
    frame = pd.DataFrame([{"symbol": "AUSDT"}])

    with pytest.raises(ValueError, match="missing required selector column"):
        _MODULE._select_signal_rows(frame, "continuation")
```

- [ ] **Step 2: Run selector tests and verify failure**

Run: `.venv/bin/pytest tests/test_validate_signal_semantics.py -q`

Expected: fails because `parse_signal_selector` is not defined.

- [ ] **Step 3: Implement registry helpers**

In `scripts/validate_ultra_signal_production.py`, add `Sequence` and `dataclass` to imports:

```python
from collections.abc import Sequence
from dataclasses import dataclass
```

Add this code after filename constants:

```python
VALIDATOR_VERSION = "v1.1"
MARKET_1M_TIMESTAMP_SEMANTICS = "minute_open_utc"
TIMESTAMP_SEMANTICS = "hour_bucket_start_utc"
ENTRY_POLICY = "hour_close_proxy"
FORWARD_SCAN_START_POLICY = "signal_available_at_inclusive"
PRIMARY_LABEL = "+10_before_-8"
PRIMARY_HORIZON_HOURS = 24


@dataclass(frozen=True)
class SignalFamilyDefinition:
    name: str
    slug: str
    title: str
    count_key: str
    selector_column: str
    grades: Sequence[str]
    emit_gate_flow: bool = False

    @property
    def supports_grades(self) -> bool:
        return bool(self.grades)


SIGNAL_FAMILY_REGISTRY: dict[str, SignalFamilyDefinition] = {
    "continuation": SignalFamilyDefinition(
        name="continuation",
        slug="continuation",
        title="Continuation",
        count_key="continuation_signal_count",
        selector_column="continuation_grade",
        grades=("A", "B"),
    ),
    "ignition": SignalFamilyDefinition(
        name="ignition",
        slug="ignition",
        title="Ignition",
        count_key="ignition_signal_count",
        selector_column="ignition_grade",
        grades=("EXTREME", "A", "B"),
    ),
    "reacceleration": SignalFamilyDefinition(
        name="reacceleration",
        slug="reacceleration",
        title="Reacceleration",
        count_key="reacceleration_signal_count",
        selector_column="reacceleration_grade",
        grades=("A", "B"),
    ),
    "ultra_high_conviction": SignalFamilyDefinition(
        name="ultra_high_conviction",
        slug="ultra",
        title="Ultra High Conviction",
        count_key="ultra_signal_count",
        selector_column="ultra_high_conviction",
        grades=(),
        emit_gate_flow=True,
    ),
}


def parse_signal_selector(value: str) -> tuple[SignalFamilyDefinition, str | None, str]:
    normalized = value.strip()
    if normalized == "ultra":
        normalized = "ultra_high_conviction"
    if normalized in SIGNAL_FAMILY_REGISTRY:
        family = SIGNAL_FAMILY_REGISTRY[normalized]
        return family, None, family.name
    for family in SIGNAL_FAMILY_REGISTRY.values():
        prefix = f"{family.name}_"
        if not normalized.startswith(prefix):
            continue
        grade = normalized[len(prefix) :]
        if not family.supports_grades or grade not in family.grades:
            raise ValueError(f"unsupported grade selector: {value}")
        return family, grade, normalized
    supported = ", ".join(sorted(SIGNAL_FAMILY_REGISTRY))
    raise ValueError(f"unsupported signal family: {value}. supported values: {supported}")
```

Replace `_normalize_signal_family`, `_signal_family_slug`, `_signal_family_title`, and `_signal_count_key` with registry-backed versions:

```python
def _normalize_signal_family(value: str) -> str:
    family, grade, selector = parse_signal_selector(value)
    return selector if grade is not None else family.name


def _signal_family_slug(signal_family: str) -> str:
    family, grade, _selector = parse_signal_selector(signal_family)
    if grade is None:
        return family.slug
    return f"{family.slug}-{grade.lower()}"


def _signal_family_title(signal_family: str) -> str:
    family, grade, _selector = parse_signal_selector(signal_family)
    if grade is None:
        return family.title
    return f"{family.title} {grade}"


def _signal_count_key(signal_family: str) -> str:
    family, grade, _selector = parse_signal_selector(signal_family)
    if grade is None:
        return family.count_key
    return f"{family.name}_{grade.lower()}_signal_count"
```

Replace `_select_signal_rows` with:

```python
def _select_signal_rows(window: pd.DataFrame, signal_family: str) -> pd.DataFrame:
    family, grade, _selector = parse_signal_selector(signal_family)
    column = family.selector_column
    if column not in window.columns:
        raise ValueError(f"missing required selector column: {column}")
    if family.name == "ultra_high_conviction":
        return window[window[column].fillna(False).eq(True)].copy()
    values = window[column].fillna("").astype(str)
    if grade is None:
        return window[values.ne("")].copy()
    return window[values.eq(grade)].copy()
```

- [ ] **Step 4: Run selector tests and full existing validator tests**

Run: `.venv/bin/pytest tests/test_validate_signal_semantics.py tests/test_validate_ultra_signal_production.py -q`

Expected: selector tests and existing validator tests pass. If an existing assertion depends on old family config keys, update that assertion in this task before committing.

- [ ] **Step 5: Commit selector registry**

```bash
git add scripts/validate_ultra_signal_production.py tests/test_validate_signal_semantics.py
git commit -m "feat(validation): add signal family registry"
```

---

### Task 2: Implement Timestamp And Window Semantics

**Files:**
- Modify: `scripts/validate_ultra_signal_production.py`
- Modify: `tests/test_validate_signal_semantics.py`

- [ ] **Step 1: Add failing timestamp helper tests**

Append to `tests/test_validate_signal_semantics.py`:

```python
from datetime import datetime, timezone


def test_hour_bucket_start_and_signal_available_at():
    ts = pd.Timestamp("2026-04-22T10:37:15Z")

    assert _MODULE.hour_bucket_start(ts).isoformat() == "2026-04-22T10:00:00+00:00"
    assert _MODULE.signal_available_at(pd.Timestamp("2026-04-22T10:00:00Z")).isoformat() == "2026-04-22T11:00:00+00:00"


def test_default_validation_window_ends_24h_before_run_time():
    now = datetime(2026, 4, 25, 10, 34, 22, tzinfo=timezone.utc)

    start, end = _MODULE.default_validation_window(30, now=now)

    assert start.isoformat() == "2026-03-25T10:00:00+00:00"
    assert end.isoformat() == "2026-04-24T10:00:00+00:00"


def test_default_validation_window_rejects_invalid_days():
    with pytest.raises(ValueError, match="window_days must be >= 1"):
        _MODULE.default_validation_window(0, now=datetime(2026, 4, 25, tzinfo=timezone.utc))
```

- [ ] **Step 2: Run timestamp tests and verify failure**

Run: `.venv/bin/pytest tests/test_validate_signal_semantics.py::test_hour_bucket_start_and_signal_available_at tests/test_validate_signal_semantics.py::test_default_validation_window_ends_24h_before_run_time -q`

Expected: fails because the helper functions are not defined.

- [ ] **Step 3: Implement timestamp helpers**

Add after `_parse_datetime`:

```python
def _coerce_utc_timestamp_value(value: datetime | pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(value).tz_convert("UTC") if pd.Timestamp(value).tzinfo else pd.Timestamp(value, tz="UTC")


def hour_bucket_start(value: datetime | pd.Timestamp) -> pd.Timestamp:
    return _coerce_utc_timestamp_value(value).floor("h")


def signal_available_at(signal_ts: datetime | pd.Timestamp) -> pd.Timestamp:
    return hour_bucket_start(signal_ts) + pd.Timedelta(hours=1)


def default_validation_window(window_days: int, *, now: datetime | pd.Timestamp | None = None) -> tuple[datetime, datetime]:
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    current = _coerce_utc_timestamp_value(now or datetime.now(timezone.utc)).floor("h")
    end = current - pd.Timedelta(hours=24)
    start = end - pd.Timedelta(days=window_days)
    return start.to_pydatetime(), end.to_pydatetime()
```

- [ ] **Step 4: Change hourly SQL to bucket-start timestamps**

In `fetch_hourly_bars`, replace `max(m.ts) AS ts` with:

```sql
date_trunc('hour', m.ts) AS ts,
```

Keep `GROUP BY m.asset_id, m.exchange, m.symbol, date_trunc('hour', m.ts)`.

- [ ] **Step 5: Run timestamp and validator tests**

Run: `.venv/bin/pytest tests/test_validate_signal_semantics.py tests/test_validate_ultra_signal_production.py -q`

Expected: tests pass except later metadata assertions that still expect old artifact contract.

- [ ] **Step 6: Commit timestamp semantics**

```bash
git add scripts/validate_ultra_signal_production.py tests/test_validate_signal_semantics.py
git commit -m "fix(validation): use bucket-start signal timestamps"
```

---

### Task 3: Add V1.1 Forward Path Label Engine

**Files:**
- Modify: `scripts/validate_ultra_signal_production.py`
- Modify: `tests/test_validate_signal_semantics.py`

- [ ] **Step 1: Add failing path-label tests**

Append to `tests/test_validate_signal_semantics.py`:

```python
def test_forward_rows_start_at_signal_available_at_inclusive():
    rows = pd.DataFrame(
        [
            {"ts": "2026-04-22T10:59:00Z", "open": 100.0, "high": 200.0, "low": 50.0},
            {"ts": "2026-04-22T11:00:00Z", "open": 101.0, "high": 106.0, "low": 99.0},
            {"ts": "2026-04-22T11:01:00Z", "open": 106.0, "high": 112.0, "low": 105.0},
        ]
    )

    labels = _MODULE.compute_validation_path_labels(
        signal_ts=pd.Timestamp("2026-04-22T10:00:00Z"),
        entry_price=100.0,
        future_rows=rows,
        horizons=(pd.Timedelta(hours=1),),
    )

    assert labels["entry_ts"] == "2026-04-22T11:00:00+00:00"
    assert labels["label_complete_1h"] is False
    assert labels["mfe_1h_pct"] == 12.0
    assert labels["mae_1h_pct"] == -1.0
    assert labels["abs_mae_1h_pct"] == 1.0
    assert labels["hit_10pct_1h"] is True
    assert labels["time_to_hit_10pct_minutes"] == 1.0


def test_same_bar_target_drawdown_is_conservative_drawdown_first():
    rows = pd.DataFrame(
        [
            {"ts": "2026-04-22T11:00:00Z", "open": 100.0, "high": 111.0, "low": 91.0},
        ]
    )

    labels = _MODULE.compute_validation_path_labels(
        signal_ts=pd.Timestamp("2026-04-22T10:00:00Z"),
        entry_price=100.0,
        future_rows=rows,
        horizons=(pd.Timedelta(hours=1),),
    )

    assert labels["hit_10pct_before_drawdown_8pct"] is False
    assert labels["path_order"] == "ambiguous_same_bar"
    assert labels["ambiguous_same_bar"] is True


def test_sensitivity_matrix_cell_has_denominator_and_incomplete_count():
    evaluated = [
        {"label_complete_24h": True, "path_results": {"target_5_dd_5": {"hit": True}}},
        {"label_complete_24h": True, "path_results": {"target_5_dd_5": {"hit": False}}},
        {"label_complete_24h": False, "path_results": {"target_5_dd_5": {"hit": False}}},
    ]

    matrix = _MODULE.build_sensitivity_matrix(evaluated)

    assert matrix["target_5_dd_5"] == {
        "eligible_count": 2,
        "hit_count": 1,
        "incomplete_count": 1,
        "precision": 0.5,
    }
```

- [ ] **Step 2: Run path-label tests and verify failure**

Run: `.venv/bin/pytest tests/test_validate_signal_semantics.py::test_forward_rows_start_at_signal_available_at_inclusive tests/test_validate_signal_semantics.py::test_same_bar_target_drawdown_is_conservative_drawdown_first -q`

Expected: fails because `compute_validation_path_labels` is not defined.

- [ ] **Step 3: Implement path-label constants and helpers**

Add near validation constants:

```python
SENSITIVITY_TARGETS = (0.05, 0.10, 0.15)
SENSITIVITY_DRAWDOWNS = (0.05, 0.08, 0.12)
HORIZON_TOLERANCE_MINUTES = {
    "1h": 0,
    "4h": 0,
    "24h": 2,
}
```

Add after timestamp helpers:

```python
def _format_pct_label(value: float) -> str:
    return str(int(round(value * 100)))


def _path_key(target_pct: float, drawdown_pct: float) -> str:
    return f"target_{_format_pct_label(target_pct)}_dd_{_format_pct_label(drawdown_pct)}"


def _prepare_forward_rows(future_rows: pd.DataFrame, entry_ts: pd.Timestamp, horizon_end: pd.Timestamp) -> pd.DataFrame:
    if future_rows.empty:
        return future_rows.copy()
    frame = future_rows.copy()
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True, errors="coerce", format="mixed")
    for column in ("open", "high", "low"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["ts", "high", "low"])
    frame = frame[(frame["ts"] >= entry_ts) & (frame["ts"] < horizon_end)]
    return frame.sort_values("ts").drop_duplicates(subset=["ts"], keep="last").reset_index(drop=True)


def _coverage_for_horizon(rows: pd.DataFrame, entry_ts: pd.Timestamp, horizon: pd.Timedelta, tolerance: int) -> tuple[bool, int, int]:
    expected_minutes = int(horizon.total_seconds() // 60)
    expected = pd.date_range(start=entry_ts, periods=expected_minutes, freq="min", tz="UTC")
    present = set(pd.to_datetime(rows["ts"], utc=True).tolist()) if not rows.empty else set()
    missing_count = sum(1 for ts in expected if ts not in present)
    return missing_count <= tolerance, expected_minutes, missing_count


def _evaluate_target_drawdown_path(
    rows: pd.DataFrame,
    *,
    entry_ts: pd.Timestamp,
    entry_price: float,
    target_pct: float,
    drawdown_pct: float,
) -> dict[str, Any]:
    target_price = round(entry_price * (1.0 + target_pct), 12)
    drawdown_price = round(entry_price * (1.0 - drawdown_pct), 12)
    for row in rows.itertuples(index=False):
        row_ts = _coerce_utc_timestamp_value(row.ts)
        target_hit = float(row.high) >= target_price
        drawdown_hit = float(row.low) <= drawdown_price
        minutes = round((row_ts - entry_ts).total_seconds() / 60.0, 6)
        if target_hit and drawdown_hit:
            return {"hit": False, "path_order": "ambiguous_same_bar", "time_to_hit_minutes": None, "time_to_drawdown_minutes": minutes}
        if drawdown_hit:
            return {"hit": False, "path_order": "drawdown_first", "time_to_hit_minutes": None, "time_to_drawdown_minutes": minutes}
        if target_hit:
            return {"hit": True, "path_order": "target_first", "time_to_hit_minutes": minutes, "time_to_drawdown_minutes": None}
    return {"hit": False, "path_order": "unresolved", "time_to_hit_minutes": None, "time_to_drawdown_minutes": None}
```

Add the public label function:

```python
def compute_validation_path_labels(
    *,
    signal_ts: datetime | pd.Timestamp,
    entry_price: float,
    future_rows: pd.DataFrame,
    horizons: Sequence[pd.Timedelta] = (pd.Timedelta(hours=1), pd.Timedelta(hours=4), pd.Timedelta(hours=24)),
) -> dict[str, Any]:
    close = float(entry_price)
    signal_start = hour_bucket_start(signal_ts)
    entry_ts = signal_available_at(signal_start)
    horizon_end = entry_ts + max(horizons)
    future = _prepare_forward_rows(future_rows, entry_ts, horizon_end)
    labels: dict[str, Any] = {
        "signal_ts": signal_start.isoformat(),
        "signal_available_at": entry_ts.isoformat(),
        "entry_ts": entry_ts.isoformat(),
        "entry_price": close,
        "entry_policy": ENTRY_POLICY,
        "path_results": {},
        "ambiguous_same_bar": False,
        "path_order": "unresolved",
        "next_minute_open_entry_price": None,
        "next_minute_open_entry_return_delta_pct": None,
    }
    if not future.empty and "open" in future.columns and pd.notna(future.iloc[0].get("open")):
        next_open = float(future.iloc[0]["open"])
        labels["next_minute_open_entry_price"] = next_open
        labels["next_minute_open_entry_return_delta_pct"] = round((next_open / close - 1.0) * 100.0, 6)
    for horizon in horizons:
        hours = int(horizon.total_seconds() // 3600)
        key = f"{hours}h"
        window_end = entry_ts + horizon
        window_rows = future[future["ts"] < window_end].copy()
        complete, expected_minutes, missing_count = _coverage_for_horizon(
            window_rows,
            entry_ts,
            horizon,
            HORIZON_TOLERANCE_MINUTES.get(key, 0),
        )
        labels[f"label_complete_{key}"] = complete
        labels[f"expected_minutes_{key}"] = expected_minutes
        labels[f"missing_minutes_{key}"] = missing_count
        if window_rows.empty:
            labels[f"mfe_{key}_pct"] = 0.0
            labels[f"mae_{key}_pct"] = 0.0
            labels[f"abs_mae_{key}_pct"] = 0.0
            labels[f"hit_10pct_{key}"] = False
            continue
        high = max(float(window_rows["high"].max()), close)
        low = min(float(window_rows["low"].min()), close)
        labels[f"mfe_{key}_pct"] = round(max((high / close - 1.0) * 100.0, 0.0), 6)
        mae = round(min((low / close - 1.0) * 100.0, 0.0), 6)
        labels[f"mae_{key}_pct"] = mae
        labels[f"abs_mae_{key}_pct"] = round(abs(mae), 6)
        labels[f"hit_10pct_{key}"] = labels[f"mfe_{key}_pct"] >= 10.0
    horizon_rows = future[future["ts"] < entry_ts + pd.Timedelta(hours=24)].copy()
    for target_pct in SENSITIVITY_TARGETS:
        for drawdown_pct in SENSITIVITY_DRAWDOWNS:
            result = _evaluate_target_drawdown_path(
                horizon_rows,
                entry_ts=entry_ts,
                entry_price=close,
                target_pct=target_pct,
                drawdown_pct=drawdown_pct,
            )
            key = _path_key(target_pct, drawdown_pct)
            labels["path_results"][key] = result
            if target_pct == 0.10 and drawdown_pct == 0.08:
                labels["hit_10pct_before_drawdown_8pct"] = bool(result["hit"])
                labels["hit_10pct_first"] = True if result["path_order"] == "target_first" else False
                labels["drawdown_8pct_first"] = result["path_order"] in {"drawdown_first", "ambiguous_same_bar"}
                labels["time_to_hit_10pct_minutes"] = result["time_to_hit_minutes"]
                labels["time_to_drawdown_8pct_minutes"] = result["time_to_drawdown_minutes"]
                labels["path_order"] = result["path_order"]
                labels["ambiguous_same_bar"] = result["path_order"] == "ambiguous_same_bar"
    return labels
```

Add sensitivity summary:

```python
def build_sensitivity_matrix(evaluated: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    matrix: dict[str, dict[str, float | int]] = {}
    for target_pct in SENSITIVITY_TARGETS:
        for drawdown_pct in SENSITIVITY_DRAWDOWNS:
            key = _path_key(target_pct, drawdown_pct)
            complete_rows = [row for row in evaluated if row.get("label_complete_24h")]
            incomplete_count = sum(1 for row in evaluated if not row.get("label_complete_24h"))
            hit_count = sum(1 for row in complete_rows if row.get("path_results", {}).get(key, {}).get("hit") is True)
            eligible_count = len(complete_rows)
            matrix[key] = {
                "eligible_count": eligible_count,
                "hit_count": hit_count,
                "incomplete_count": incomplete_count,
                "precision": round(hit_count / eligible_count, 6) if eligible_count else 0.0,
            }
    return matrix
```

- [ ] **Step 4: Run path-label tests**

Run: `.venv/bin/pytest tests/test_validate_signal_semantics.py -q`

Expected: new path-label tests pass.

- [ ] **Step 5: Commit path-label engine**

```bash
git add scripts/validate_ultra_signal_production.py tests/test_validate_signal_semantics.py
git commit -m "feat(validation): add v1 path label semantics"
```

---

### Task 4: Make Forward Queries And Evaluation Use Signal Availability

**Files:**
- Modify: `scripts/validate_ultra_signal_production.py`
- Modify: `tests/test_validate_signal_semantics.py`

- [ ] **Step 1: Add failing forward-query boundary test**

Append to `tests/test_validate_signal_semantics.py`:

```python
def test_fetch_forward_rows_uses_available_at_inclusive(monkeypatch):
    captured = {}

    class FakeResult:
        def mappings(self):
            return self

        def all(self):
            return []

    class FakeConnection:
        def execute(self, statement, params):
            captured["sql"] = str(statement)
            captured["params"] = params
            return FakeResult()

    class FakeBegin:
        def __enter__(self):
            return FakeConnection()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeEngine:
        def begin(self):
            return FakeBegin()

    signal_ts = pd.Timestamp("2026-04-22T10:00:00Z").to_pydatetime()
    _MODULE.fetch_forward_1m_rows(FakeEngine(), 123, signal_ts, pd.Timedelta(hours=24))

    assert "m.ts >= :signal_available_at" in captured["sql"]
    assert "m.ts < :horizon_end" in captured["sql"]
    assert captured["params"]["signal_available_at"].isoformat() == "2026-04-22T11:00:00+00:00"
    assert captured["params"]["horizon_end"].isoformat() == "2026-04-23T11:00:00+00:00"
```

- [ ] **Step 2: Run boundary test and verify failure**

Run: `.venv/bin/pytest tests/test_validate_signal_semantics.py::test_fetch_forward_rows_uses_available_at_inclusive -q`

Expected: fails because current SQL uses `m.ts > :signal_ts`.

- [ ] **Step 3: Update forward query**

Replace `fetch_forward_1m_rows` with:

```python
def fetch_forward_1m_rows(engine: Engine, asset_id: int, signal_ts: datetime, horizon: timedelta) -> pd.DataFrame:
    signal_available = signal_available_at(signal_ts).to_pydatetime()
    horizon_end = (pd.Timestamp(signal_available) + pd.Timedelta(horizon)).to_pydatetime()
    statement = text(
        """
        SELECT
            m.ts,
            m.open,
            m.high,
            m.low
        FROM alt_core.market_1m AS m
        WHERE m.asset_id = :asset_id
          AND m.ts >= :signal_available_at
          AND m.ts < :horizon_end
        ORDER BY m.ts
        """
    )
    with engine.begin() as connection:
        rows = connection.execute(
            statement,
            {"asset_id": asset_id, "signal_available_at": signal_available, "horizon_end": horizon_end},
        ).mappings().all()
    return pd.DataFrame(rows)
```

- [ ] **Step 4: Update evaluation loop to call the new label engine**

In `evaluate_signal_family`, replace:

```python
labels = compute_forward_path_labels(pd.Timestamp(signal_ts), float(row["close"]), future_1m)
```

with:

```python
labels = compute_validation_path_labels(
    signal_ts=pd.Timestamp(signal_ts),
    entry_price=float(row["close"]),
    future_rows=future_1m,
)
```

In each evaluated row dict, add these fields from `labels`:

```python
"signal_ts": labels["signal_ts"],
"signal_available_at": labels["signal_available_at"],
"entry_ts": labels["entry_ts"],
"entry_price": labels["entry_price"],
"entry_policy": labels["entry_policy"],
"label_complete_1h": labels["label_complete_1h"],
"label_complete_4h": labels["label_complete_4h"],
"label_complete_24h": labels["label_complete_24h"],
"missing_minutes_24h": labels["missing_minutes_24h"],
"abs_mae_24h_pct": labels["abs_mae_24h_pct"],
"path_order": labels["path_order"],
"ambiguous_same_bar": labels["ambiguous_same_bar"],
"path_results": labels["path_results"],
"next_minute_open_entry_price": labels["next_minute_open_entry_price"],
"next_minute_open_entry_return_delta_pct": labels["next_minute_open_entry_return_delta_pct"],
```

Keep existing fields like `"ts"` for compatibility, but set `"ts": labels["signal_ts"]`.

- [ ] **Step 5: Run validator tests**

Run: `.venv/bin/pytest tests/test_validate_signal_semantics.py tests/test_validate_ultra_signal_production.py -q`

Expected: boundary tests and existing validator tests pass. If a row-shape assertion depends on the old forward-label keys, update that assertion in this task before committing.

- [ ] **Step 6: Commit evaluation boundary changes**

```bash
git add scripts/validate_ultra_signal_production.py tests/test_validate_signal_semantics.py
git commit -m "fix(validation): scan forward rows after availability"
```

---

### Task 5: Make Summaries Coverage-Aware

**Files:**
- Modify: `scripts/validate_ultra_signal_production.py`
- Modify: `tests/test_validate_ultra_signal_production.py`
- Modify: `tests/test_validate_signal_semantics.py`

- [ ] **Step 1: Add failing summary denominator test**

Append to `tests/test_validate_signal_semantics.py`:

```python
def test_summarize_evaluated_signals_excludes_incomplete_labels_from_denominator():
    rows = [
        {
            "label_complete_1h": True,
            "label_complete_4h": True,
            "label_complete_24h": True,
            "hit_10pct_1h": True,
            "hit_10pct_4h": True,
            "hit_10pct_24h": True,
            "hit_10pct_before_drawdown_8pct": True,
            "hit_10pct_first": True,
            "drawdown_8pct_first": False,
            "ambiguous_same_bar": False,
            "mfe_24h_pct": 20.0,
            "mae_24h_pct": -3.0,
            "abs_mae_24h_pct": 3.0,
            "time_to_hit_10pct_minutes": 5.0,
            "path_results": {"target_10_dd_8": {"hit": True}},
        },
        {
            "label_complete_1h": True,
            "label_complete_4h": True,
            "label_complete_24h": False,
            "hit_10pct_1h": False,
            "hit_10pct_4h": False,
            "hit_10pct_24h": False,
            "hit_10pct_before_drawdown_8pct": False,
            "hit_10pct_first": False,
            "drawdown_8pct_first": False,
            "ambiguous_same_bar": False,
            "mfe_24h_pct": 0.0,
            "mae_24h_pct": 0.0,
            "abs_mae_24h_pct": 0.0,
            "time_to_hit_10pct_minutes": None,
            "path_results": {"target_10_dd_8": {"hit": False}},
        },
    ]

    summary = _MODULE.summarize_evaluated_signals(rows, signal_family="ignition")

    assert summary["signal_count"] == 2
    assert summary["primary_label_complete_count"] == 1
    assert summary["incomplete_label_count"] == 1
    assert summary["hit10_24h_rate"] == 1.0
    assert summary["precision_before_dd8"] == 1.0
    assert summary["avg_mae_24h_pct"] == -3.0
    assert summary["avg_abs_mae_24h_pct"] == 3.0
    assert summary["ambiguous_same_bar_count"] == 0
```

- [ ] **Step 2: Run summary test and verify failure**

Run: `.venv/bin/pytest tests/test_validate_signal_semantics.py::test_summarize_evaluated_signals_excludes_incomplete_labels_from_denominator -q`

Expected: fails because current summary uses total signal count as denominator and positive MAE.

- [ ] **Step 3: Replace summary helpers**

Add helper near `_mean_numeric`:

```python
def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0
```

Replace `summarize_evaluated_signals` with:

```python
def summarize_evaluated_signals(
    evaluated: list[dict[str, Any]],
    *,
    signal_family: str = "ultra_high_conviction",
) -> dict[str, Any]:
    signal_count = len(evaluated)
    complete_1h = [row for row in evaluated if row.get("label_complete_1h", True)]
    complete_4h = [row for row in evaluated if row.get("label_complete_4h", True)]
    complete_24h = [row for row in evaluated if row.get("label_complete_24h", True)]
    hit_1h_count = sum(1 for row in complete_1h if row.get("hit_10pct_1h"))
    hit_4h_count = sum(1 for row in complete_4h if row.get("hit_10pct_4h"))
    hit_24h_count = sum(1 for row in complete_24h if row.get("hit_10pct_24h"))
    strict_hit_count = sum(1 for row in complete_24h if row.get("hit_10pct_before_drawdown_8pct"))
    hit_10pct_first_count = sum(1 for row in complete_24h if row.get("hit_10pct_first") is True)
    drawdown_8pct_first_count = sum(1 for row in complete_24h if row.get("drawdown_8pct_first") is True)
    ambiguous_same_bar_count = sum(1 for row in complete_24h if row.get("ambiguous_same_bar") is True)
    incomplete_label_count = signal_count - len(complete_24h)
    count_key = _signal_count_key(signal_family)
    summary = {
        "signal_family": signal_family,
        "signal_count": signal_count,
        count_key: signal_count,
        "primary_label_complete_count": len(complete_24h),
        "incomplete_label_count": incomplete_label_count,
        "hit_10_1h_count": hit_1h_count,
        "hit_10_4h_count": hit_4h_count,
        "hit_10_24h_count": hit_24h_count,
        "hit_10_before_dd8_count": strict_hit_count,
        "hit_10pct_first_count": hit_10pct_first_count,
        "drawdown_8pct_first_count": drawdown_8pct_first_count,
        "ambiguous_same_bar_count": ambiguous_same_bar_count,
        "hit10_1h_rate": _rate(hit_1h_count, len(complete_1h)),
        "hit10_4h_rate": _rate(hit_4h_count, len(complete_4h)),
        "hit10_24h_rate": _rate(hit_24h_count, len(complete_24h)),
        "precision_1h": _rate(hit_1h_count, len(complete_1h)),
        "precision_4h": _rate(hit_4h_count, len(complete_4h)),
        "precision_24h": _rate(hit_24h_count, len(complete_24h)),
        "precision_before_dd8": _rate(strict_hit_count, len(complete_24h)),
        "hit_10pct_first_rate": _rate(hit_10pct_first_count, len(complete_24h)),
        "drawdown_8pct_first_rate": _rate(drawdown_8pct_first_count, len(complete_24h)),
        "avg_mfe_1h_pct": _mean_numeric([row.get("mfe_1h_pct") for row in complete_1h]),
        "avg_mfe_24h_pct": _mean_numeric([row.get("mfe_24h_pct") for row in complete_24h]),
        "avg_mae_24h_pct": _mean_numeric([row.get("mae_24h_pct") for row in complete_24h]),
        "avg_abs_mae_24h_pct": _mean_numeric([row.get("abs_mae_24h_pct") for row in complete_24h]),
        "median_time_to_hit_10pct_minutes": _median_numeric(
            [row.get("time_to_hit_10pct_minutes") for row in complete_24h if row.get("time_to_hit_10pct_minutes") is not None]
        ),
        "median_time_to_drawdown_8pct_minutes": _median_numeric(
            [row.get("time_to_drawdown_8pct_minutes") for row in complete_24h if row.get("time_to_drawdown_8pct_minutes") is not None]
        ),
        "sensitivity_matrix": build_sensitivity_matrix(evaluated),
    }
    summary["unresolved_24h_count"] = sum(
        1
        for row in complete_24h
        if row.get("hit_10pct_first") is None and row.get("drawdown_8pct_first") is None
    )
    return summary
```

- [ ] **Step 4: Update existing summary test expectations**

In `tests/test_validate_ultra_signal_production.py::test_summarize_evaluated_signals_reports_path_risk_metrics`, update row fixtures so each row has:

```python
"label_complete_1h": True,
"label_complete_4h": True,
"label_complete_24h": True,
"abs_mae_24h_pct": 3.0,
"ambiguous_same_bar": False,
"path_results": {"target_10_dd_8": {"hit": True}},
```

If the existing fixture uses positive `mae_24h_pct`, change those values to negative values and assert:

```python
assert summary["avg_mae_24h_pct"] == -7.0
assert summary["avg_abs_mae_24h_pct"] == 7.0
assert summary["hit10_24h_rate"] == 0.666667
```

- [ ] **Step 5: Run summary tests**

Run: `.venv/bin/pytest tests/test_validate_signal_semantics.py tests/test_validate_ultra_signal_production.py -q`

Expected: all summary tests pass.

- [ ] **Step 6: Commit coverage-aware summaries**

```bash
git add scripts/validate_ultra_signal_production.py tests/test_validate_signal_semantics.py tests/test_validate_ultra_signal_production.py
git commit -m "feat(validation): summarize complete labels only"
```

---

### Task 6: Update Metadata, Signals CSV, And README Contract

**Files:**
- Modify: `scripts/validate_ultra_signal_production.py`
- Modify: `tests/test_validate_ultra_signal_production.py`

- [ ] **Step 1: Update failing metadata contract test**

In `tests/test_validate_ultra_signal_production.py::test_build_run_metadata_captures_validation_contract`, replace old assertions with:

```python
assert metadata["validator_version"] == "v1.1"
assert metadata["entry_policy"] == "hour_close_proxy"
assert metadata["market_1m_timestamp_semantics"] == "minute_open_utc"
assert metadata["timestamp_semantics"] == "hour_bucket_start_utc"
assert metadata["forward_scan_start_policy"] == "signal_available_at_inclusive"
assert metadata["primary_label"] == "+10_before_-8"
assert metadata["horizon_hours"] == 24
assert metadata["coverage_status"] == "trusted"
assert metadata["validation_window"] == {
    "from": "2026-01-22T10:00:00+00:00",
    "to": "2026-04-22T10:00:00+00:00",
}
assert metadata["symbol_allowlist"] == []
assert metadata["symbol_blocklist"] == []
assert metadata["missing_optional_columns"] == []
```

In `test_write_artifacts_writes_summary_signals_metadata_and_readme`, update `rows` to include all minimum CSV fields:

```python
rows = [
    {
        "exchange": "binance",
        "symbol": "HIGHUSDT",
        "signal_family": "ultra_high_conviction",
        "signal_grade": "",
        "signal_ts": "2026-04-22T10:00:00+00:00",
        "signal_available_at": "2026-04-22T11:00:00+00:00",
        "entry_ts": "2026-04-22T11:00:00+00:00",
        "entry_price": 100.0,
        "entry_policy": "hour_close_proxy",
        "label_complete_24h": True,
        "hit_10_before_dd8": True,
        "mfe_24h_pct": 12.0,
        "mae_24h_pct": -4.0,
        "abs_mae_24h_pct": 4.0,
        "time_to_hit_10pct_minutes": 12.0,
        "path_order": "target_first",
    }
]
```

Assert README contains:

```python
assert "validator_version: v1.1" in readme
assert "entry_policy: hour_close_proxy" in readme
assert "forward_scan_start_policy: signal_available_at_inclusive" in readme
assert "hit10_24h_rate" in readme
assert "avg_abs_mae_24h_pct" in readme
```

- [ ] **Step 2: Run artifact tests and verify failure**

Run: `.venv/bin/pytest tests/test_validate_ultra_signal_production.py::test_build_run_metadata_captures_validation_contract tests/test_validate_ultra_signal_production.py::test_write_artifacts_writes_summary_signals_metadata_and_readme -q`

Expected: fails because metadata still uses old contract.

- [ ] **Step 3: Implement metadata v1.1 contract**

Update `build_run_metadata` signature:

```python
def build_run_metadata(
    *,
    exchange: str,
    start: datetime,
    end: datetime,
    market_start: datetime,
    market_end: datetime,
    output_dir: Path,
    output_root: Path,
    signal_family: str = "ultra_high_conviction",
    generated_at: datetime | None = None,
    git_sha: str = "unknown",
    symbol_allowlist: list[str] | None = None,
    symbol_blocklist: list[str] | None = None,
    coverage_status: str = "trusted",
    primary_label_complete_count: int = 0,
    incomplete_label_count: int = 0,
    missing_optional_columns: list[str] | None = None,
) -> dict[str, Any]:
```

At the start of the function:

```python
signal_family = _normalize_signal_family(signal_family)
family, _grade, selector = parse_signal_selector(signal_family)
generated = _coerce_utc_datetime(generated_at or datetime.now(timezone.utc))
allowlist = list(symbol_allowlist or [])
blocklist = list(symbol_blocklist or [])
```

Return a dict that includes the old nested fields plus the v1.1 top-level contract:

```python
"validator_version": VALIDATOR_VERSION,
"git_sha": git_sha,
"run_started_at": generated.isoformat(),
"window_start": start.isoformat(),
"window_end": end.isoformat(),
"exchange_universe": [exchange],
"symbol_allowlist": allowlist,
"symbol_blocklist": blocklist,
"family": family.name,
"selector": selector,
"rule_version": f"{family.name}:{VALIDATOR_VERSION}",
"feature_preparation_version": git_sha,
"entry_policy": ENTRY_POLICY,
"market_1m_timestamp_semantics": MARKET_1M_TIMESTAMP_SEMANTICS,
"timestamp_semantics": TIMESTAMP_SEMANTICS,
"forward_scan_start_policy": FORWARD_SCAN_START_POLICY,
"primary_label": PRIMARY_LABEL,
"horizon_hours": PRIMARY_HORIZON_HOURS,
"primary_label_complete_count": primary_label_complete_count,
"incomplete_label_count": incomplete_label_count,
"coverage_status": coverage_status,
"missing_optional_columns": list(missing_optional_columns or []),
```

- [ ] **Step 4: Update README builder**

In `build_run_readme`, add these lines after `generated_at`:

```python
f"- validator_version: {metadata.get('validator_version', VALIDATOR_VERSION)}",
f"- entry_policy: {metadata.get('entry_policy', ENTRY_POLICY)}",
f"- timestamp_semantics: {metadata.get('timestamp_semantics', TIMESTAMP_SEMANTICS)}",
f"- forward_scan_start_policy: {metadata.get('forward_scan_start_policy', FORWARD_SCAN_START_POLICY)}",
f"- coverage_status: {metadata.get('coverage_status', 'trusted')}",
```

In the Snapshot section, replace precision lines with:

```python
f"- hit10_1h_rate: {summary.get('hit10_1h_rate', summary.get('precision_1h', 0.0))}",
f"- hit10_4h_rate: {summary.get('hit10_4h_rate', summary.get('precision_4h', 0.0))}",
f"- hit10_24h_rate: {summary.get('hit10_24h_rate', summary.get('precision_24h', 0.0))}",
f"- precision_before_dd8: {summary['precision_before_dd8']}",
f"- avg_abs_mae_24h_pct: {summary.get('avg_abs_mae_24h_pct', 0.0)}",
f"- ambiguous_same_bar_count: {summary.get('ambiguous_same_bar_count', 0)}",
f"- incomplete_label_count: {summary.get('incomplete_label_count', 0)}",
```

- [ ] **Step 5: Make signals CSV preserve minimum columns**

Add this constant near filenames:

```python
SIGNALS_MINIMUM_COLUMNS = [
    "exchange",
    "symbol",
    "signal_family",
    "signal_grade",
    "signal_ts",
    "signal_available_at",
    "entry_ts",
    "entry_price",
    "entry_policy",
    "label_complete_24h",
    "hit_10_before_dd8",
    "mfe_24h_pct",
    "mae_24h_pct",
    "abs_mae_24h_pct",
    "time_to_hit_10pct_minutes",
    "path_order",
]
```

In `write_artifacts`, when rows exist, compute fieldnames:

```python
extra_columns = sorted({key for row in rows for key in row.keys()} - set(SIGNALS_MINIMUM_COLUMNS))
writer = csv.DictWriter(handle, fieldnames=SIGNALS_MINIMUM_COLUMNS + extra_columns)
```

- [ ] **Step 6: Run artifact tests**

Run: `.venv/bin/pytest tests/test_validate_ultra_signal_production.py -q`

Expected: all artifact tests pass.

- [ ] **Step 7: Commit artifact contract**

```bash
git add scripts/validate_ultra_signal_production.py tests/test_validate_ultra_signal_production.py
git commit -m "feat(validation): record v1 metadata contract"
```

---

### Task 7: Add CLI Window Flags And Compatibility

**Files:**
- Modify: `scripts/validate_ultra_signal_production.py`
- Modify: `tests/test_validate_signal_semantics.py`

- [ ] **Step 1: Add failing CLI window resolution tests**

Append to `tests/test_validate_signal_semantics.py`:

```python
def test_resolve_validation_window_uses_explicit_from_to():
    start, end = _MODULE.resolve_validation_window(
        start_value="2026-03-23T00:00:00Z",
        end_value="2026-04-22T00:00:00Z",
        window_days=None,
        now=datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
    )

    assert start.isoformat() == "2026-03-23T00:00:00+00:00"
    assert end.isoformat() == "2026-04-22T00:00:00+00:00"


def test_resolve_validation_window_uses_window_days_and_end_at():
    start, end = _MODULE.resolve_validation_window(
        start_value=None,
        end_value="2026-04-24T00:00:00Z",
        window_days=30,
        now=datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
    )

    assert start.isoformat() == "2026-03-25T00:00:00+00:00"
    assert end.isoformat() == "2026-04-24T00:00:00+00:00"
```

- [ ] **Step 2: Run CLI window tests and verify failure**

Run: `.venv/bin/pytest tests/test_validate_signal_semantics.py::test_resolve_validation_window_uses_explicit_from_to tests/test_validate_signal_semantics.py::test_resolve_validation_window_uses_window_days_and_end_at -q`

Expected: fails because `resolve_validation_window` is not defined.

- [ ] **Step 3: Implement window resolver**

Add after `default_validation_window`:

```python
def resolve_validation_window(
    *,
    start_value: str | None,
    end_value: str | None,
    window_days: int | None,
    now: datetime | pd.Timestamp | None = None,
) -> tuple[datetime, datetime]:
    if start_value and end_value:
        start = _parse_datetime(start_value)
        end = _parse_datetime(end_value)
    elif window_days is not None and end_value:
        end = _parse_datetime(end_value)
        start = (pd.Timestamp(end) - pd.Timedelta(days=window_days)).to_pydatetime()
    elif window_days is not None:
        start, end = default_validation_window(window_days, now=now)
    else:
        raise ValueError("provide --from and --to, or provide --window-days with optional --end-at")
    if start >= end:
        raise ValueError("start must be earlier than end")
    return start, end
```

- [ ] **Step 4: Update argparse flags**

In `main`, change parser setup to:

```python
parser = argparse.ArgumentParser()
parser.add_argument("--from", dest="start")
parser.add_argument("--to", dest="end")
parser.add_argument("--window-days", type=int)
parser.add_argument("--end-at", dest="end_at")
parser.add_argument("--exchange", default="binance")
parser.add_argument("--signal-family", default="ultra_high_conviction")
parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT)
parser.add_argument("--compare-baseline-config")
parser.add_argument("--compare-candidate-config")
parser.add_argument("--require-90d", action="store_true")
```

Replace current start/end parsing with:

```python
start, end = resolve_validation_window(
    start_value=args.start,
    end_value=args.end_at or args.end,
    window_days=args.window_days,
)
```

- [ ] **Step 5: Run CLI-related tests**

Run: `.venv/bin/pytest tests/test_validate_signal_semantics.py tests/test_validate_ultra_signal_production.py -q`

Expected: all current validator tests pass.

- [ ] **Step 6: Commit CLI flags**

```bash
git add scripts/validate_ultra_signal_production.py tests/test_validate_signal_semantics.py
git commit -m "feat(validation): add fixed window flags"
```

---

### Task 8: Add Comparison Policy Helpers

**Files:**
- Modify: `scripts/validate_ultra_signal_production.py`
- Modify: `tests/test_validate_signal_semantics.py`

- [ ] **Step 1: Add failing comparison policy tests**

Append to `tests/test_validate_signal_semantics.py`:

```python
def _comparison_metadata(window_start="2026-03-25T00:00:00+00:00", window_end="2026-04-24T00:00:00+00:00"):
    return {
        "window_start": window_start,
        "window_end": window_end,
        "exchange_universe": ["binance"],
        "symbol_allowlist": [],
        "symbol_blocklist": [],
        "feature_preparation_version": "abc123",
        "selector": "ignition",
        "timestamp_semantics": "hour_bucket_start_utc",
        "entry_policy": "hour_close_proxy",
        "primary_label": "+10_before_-8",
        "coverage_status": "trusted",
        "rule_version": "rule:v1",
        "git_sha": "abc123",
    }


def test_compare_validation_runs_rejects_window_mismatch():
    baseline = {"metadata": _comparison_metadata(), "summary": {"signal_count": 20}}
    candidate = {
        "metadata": _comparison_metadata(window_end="2026-04-25T00:00:00+00:00"),
        "summary": {"signal_count": 20},
    }

    result = _MODULE.compare_validation_runs(baseline, candidate, require_90d=False)

    assert result["status"] == "insufficient"
    assert result["reason"] == "comparison_window_mismatch"


def test_compare_validation_runs_marks_sample_limited():
    baseline = {
        "metadata": _comparison_metadata(),
        "summary": {"signal_count": 4, "precision_before_dd8": 0.5, "avg_abs_mae_24h_pct": 10.0},
    }
    candidate = {
        "metadata": _comparison_metadata(),
        "summary": {"signal_count": 4, "precision_before_dd8": 0.75, "avg_abs_mae_24h_pct": 7.0},
    }

    result = _MODULE.compare_validation_runs(baseline, candidate, require_90d=False)

    assert result["status"] == "experimental_only"
    assert result["reason"] == "sample_limited"
```

- [ ] **Step 2: Run comparison tests and verify failure**

Run: `.venv/bin/pytest tests/test_validate_signal_semantics.py::test_compare_validation_runs_rejects_window_mismatch tests/test_validate_signal_semantics.py::test_compare_validation_runs_marks_sample_limited -q`

Expected: fails because comparison helpers are not defined.

- [ ] **Step 3: Implement comparison helpers**

Add near artifact helpers:

```python
COMPARISON_MATCH_FIELDS = (
    "window_start",
    "window_end",
    "exchange_universe",
    "symbol_allowlist",
    "symbol_blocklist",
    "feature_preparation_version",
    "selector",
    "timestamp_semantics",
    "entry_policy",
    "primary_label",
)


def compare_validation_runs(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    require_90d: bool,
) -> dict[str, Any]:
    baseline_metadata = baseline["metadata"]
    candidate_metadata = candidate["metadata"]
    for field in COMPARISON_MATCH_FIELDS:
        if baseline_metadata.get(field) != candidate_metadata.get(field):
            return {"status": "insufficient", "reason": "comparison_window_mismatch", "mismatched_field": field}
    if candidate_metadata.get("coverage_status") != "trusted":
        return {"status": "insufficient", "reason": candidate_metadata.get("coverage_status", "coverage_not_trusted")}
    baseline_summary = baseline["summary"]
    candidate_summary = candidate["summary"]
    baseline_count = int(baseline_summary.get("signal_count", 0))
    candidate_count = int(candidate_summary.get("signal_count", 0))
    if baseline_count < 20 or candidate_count < 10:
        return {
            "status": "experimental_only",
            "reason": "sample_limited",
            "baseline_count": baseline_count,
            "candidate_count": candidate_count,
        }
    baseline_precision = float(baseline_summary.get("precision_before_dd8", 0.0))
    candidate_precision = float(candidate_summary.get("precision_before_dd8", 0.0))
    baseline_abs_mae = float(baseline_summary.get("avg_abs_mae_24h_pct", 0.0))
    candidate_abs_mae = float(candidate_summary.get("avg_abs_mae_24h_pct", 0.0))
    count_floor = baseline_count * 0.8
    evidence_backed = (
        candidate_precision >= baseline_precision
        and candidate_abs_mae < baseline_abs_mae
        and candidate_count >= count_floor
    )
    return {
        "status": "evidence_backed" if evidence_backed else "not_supported",
        "reason": "metrics_pass" if evidence_backed else "metrics_do_not_pass",
        "baseline_count": baseline_count,
        "candidate_count": candidate_count,
        "baseline_precision_before_dd8": baseline_precision,
        "candidate_precision_before_dd8": candidate_precision,
        "baseline_avg_abs_mae_24h_pct": baseline_abs_mae,
        "candidate_avg_abs_mae_24h_pct": candidate_abs_mae,
        "requires_90d": require_90d,
    }
```

Add file loader:

```python
def load_comparison_config(path: str) -> dict[str, Any]:
    config_path = Path(path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    summary_path = Path(config["summary_path"])
    metadata_path = Path(config["metadata_path"])
    return {
        "summary": json.loads(summary_path.read_text(encoding="utf-8")),
        "metadata": json.loads(metadata_path.read_text(encoding="utf-8")),
    }
```

- [ ] **Step 4: Wire comparison flags into main**

At the start of `main`, after `args = parser.parse_args()`, add:

```python
if args.compare_baseline_config or args.compare_candidate_config:
    if not args.compare_baseline_config or not args.compare_candidate_config:
        raise ValueError("--compare-baseline-config and --compare-candidate-config must be provided together")
    baseline = load_comparison_config(args.compare_baseline_config)
    candidate = load_comparison_config(args.compare_candidate_config)
    result = compare_validation_runs(baseline, candidate, require_90d=bool(args.require_90d))
    print(json.dumps(result, sort_keys=True))
    return 0
```

- [ ] **Step 5: Run comparison tests**

Run: `.venv/bin/pytest tests/test_validate_signal_semantics.py -q`

Expected: comparison tests pass.

- [ ] **Step 6: Commit comparison policy**

```bash
git add scripts/validate_ultra_signal_production.py tests/test_validate_signal_semantics.py
git commit -m "feat(validation): add comparison policy checks"
```

---

### Task 9: Add Optional Real DB Smoke Test

**Files:**
- Create: `tests/test_validate_signal_db_smoke.py`

- [ ] **Step 1: Add environment-gated DB smoke test**

Create `tests/test_validate_signal_db_smoke.py`:

```python
import importlib.util
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from altcoin_trend.config import load_settings
from altcoin_trend.db import build_engine


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_ultra_signal_production.py"
_SPEC = importlib.util.spec_from_file_location("validate_ultra_signal_production", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


@pytest.mark.skipif(os.environ.get("ACTS_RUN_DB_SMOKE") != "1", reason="set ACTS_RUN_DB_SMOKE=1 to run real DB validation smoke")
def test_real_db_validation_smoke_generates_summary():
    settings = load_settings()
    engine = build_engine(settings)
    start = datetime(2026, 4, 15, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 16, 0, 0, tzinfo=timezone.utc)

    summary, rows = _MODULE.evaluate_signal_family(
        engine,
        "binance",
        start,
        end,
        signal_family="ultra_high_conviction",
    )

    assert summary["signal_family"] == "ultra_high_conviction"
    assert "precision_before_dd8" in summary
    assert isinstance(rows, list)
```

- [ ] **Step 2: Run smoke test without DB flag**

Run: `.venv/bin/pytest tests/test_validate_signal_db_smoke.py -q`

Expected: one skipped test.

- [ ] **Step 3: Run normal validator tests**

Run: `.venv/bin/pytest tests/test_validate_signal_semantics.py tests/test_validate_ultra_signal_production.py tests/test_validate_signal_db_smoke.py -q`

Expected: semantic and artifact tests pass; DB smoke test skips.

- [ ] **Step 4: Commit DB smoke test**

```bash
git add tests/test_validate_signal_db_smoke.py
git commit -m "test(validation): add optional db smoke test"
```

---

### Task 10: Final Verification And Documentation Check

**Files:**
- Modify only if prior tasks missed a required README line: `scripts/validate_ultra_signal_production.py`
- Test: all validator tests

- [ ] **Step 1: Run targeted validator suite**

Run:

```bash
.venv/bin/pytest tests/test_validate_signal_semantics.py tests/test_validate_ultra_signal_production.py tests/test_validate_signal_db_smoke.py -q
```

Expected: all non-DB tests pass and DB smoke skips unless `ACTS_RUN_DB_SMOKE=1`.

- [ ] **Step 2: Run broader impacted suite**

Run:

```bash
.venv/bin/pytest tests/test_trade_backtest.py tests/test_signal_v2.py tests/test_validate_signal_semantics.py tests/test_validate_ultra_signal_production.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run script help smoke**

Run:

```bash
.venv/bin/python scripts/validate_ultra_signal_production.py --help
```

Expected output contains all canonical flags:

```text
--signal-family
--window-days
--end-at
--compare-baseline-config
--compare-candidate-config
--require-90d
```

- [ ] **Step 4: Verify dirty worktree scope**

Run: `git status --short`

Expected: only files from this plan are modified or staged for the final commit. Existing unrelated dirty files may still appear if they were present before this plan; do not add them.

- [ ] **Step 5: Commit final verification changes**

If Step 1 through Step 4 required code changes, run:

```bash
git add scripts/validate_ultra_signal_production.py tests/test_validate_signal_semantics.py tests/test_validate_ultra_signal_production.py tests/test_validate_signal_db_smoke.py
git commit -m "test(validation): verify validation trust workflow"
```

If Step 1 through Step 4 required no code changes, do not create an empty commit.

---

## Self-Review Checklist

Spec coverage:
- Timestamp semantics are covered by Tasks 2, 3, and 4.
- Entry policy and next-minute-open diagnostics are covered by Task 3.
- Same-bar target/drawdown ambiguity is covered by Task 3.
- Coverage-aware denominators and MAE directionality are covered by Task 5.
- Minimum artifact contract is covered by Task 6.
- Canonical script flags are covered by Task 7.
- Before/after comparison policy is covered by Task 8.
- Optional DB smoke testing is covered by Task 9.

Placeholder scan:
- This plan contains no placeholder markers, no deferred implementation sections, and no unnamed edge-case steps.
- Every task lists exact files, exact tests, exact commands, and a commit command.

Type consistency:
- `signal_ts`, `signal_available_at`, and `entry_ts` are ISO strings in artifact rows.
- `hour_bucket_start` and `signal_available_at` return UTC `pd.Timestamp` values internally.
- MAE summary uses `avg_mae_24h_pct` as a negative percentage and `avg_abs_mae_24h_pct` as positive magnitude.
- Per-row adverse excursion uses `abs_mae_24h_pct`; aggregate adverse excursion uses `avg_abs_mae_24h_pct`.
