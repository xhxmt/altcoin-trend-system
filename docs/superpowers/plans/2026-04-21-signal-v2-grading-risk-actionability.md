# Signal v2 Grading, Risk, and Actionability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the signal v2 model that grades continuation and ignition signals, adds risk/actionability scoring, improves alert priority, and upgrades backtests by signal grade.

**Architecture:** Keep the existing `final_score`, `tier`, and `trade_candidate` compatibility path intact. Add a focused signal v2 helper module for grading/risk/actionability, wire it into `scheduler.py`, then layer alert priority and v2 backtesting on top. Use additive SQL migrations and staged commits so each phase is testable on its own.

**Tech Stack:** Python 3.12+, pandas, SQLAlchemy, psycopg JSONB wrappers, Typer CLI, pytest.

---

## Spec Reference

Implement the approved design:

`docs/superpowers/specs/2026-04-21-signal-v2-grading-risk-actionability-design.md`

The repository currently has unrelated uncommitted changes for the earlier continuation/ignition iteration. Do not revert them. Stage and commit only files touched by the task being completed.

## File Structure

Create or modify these files:

- Create: `sql/006_signal_v2_fields.sql`
  - Add database columns for v2 fields.
- Create: `src/altcoin_trend/migrations/006_signal_v2_fields.sql`
  - Packaged copy of the same migration.
- Create: `src/altcoin_trend/signals/v2.py`
  - Pure helpers for ranks, volume impulse, grading, risk flags, chase risk, signal priority, and actionability.
- Create: `tests/test_signal_v2.py`
  - Unit tests for v2 helpers.
- Modify: `src/altcoin_trend/scheduler.py`
  - Compute `volume_ratio_1h`, ranks, v2 signal fields, cross-exchange confirmation, rank payload fields, and query fields.
- Modify: `tests/test_scheduler.py`
  - Snapshot pipeline tests for v2 fields and legacy compatibility.
- Modify: `src/altcoin_trend/signals/alerts.py`
  - Add v2 alert types, priority cooldown, message builders, and symbol-level aggregation.
- Modify: `tests/test_alerts.py`
  - Alert classification, cooldown, dedupe, and message tests.
- Modify: `src/altcoin_trend/trade_backtest.py`
  - Add v2 grouped report and forward-label calculations.
- Modify: `tests/test_trade_backtest.py`
  - Backtest v2 grouped report and hit-before-drawdown tests.
- Modify: `src/altcoin_trend/cli.py`
  - Add opportunity ranking and v2 backtest CLI commands.
- Modify: `tests/test_cli.py`
  - CLI output tests for new commands.
- Modify: `docs/strategy/current-strategy.md`
  - Operational strategy documentation after implementation.

## Task 1: Add v2 Schema Migration

**Files:**
- Create: `sql/006_signal_v2_fields.sql`
- Create: `src/altcoin_trend/migrations/006_signal_v2_fields.sql`
- Modify: `tests/test_db_migrations.py`

- [ ] **Step 1: Write the failing migration content test**

Add this test to `tests/test_db_migrations.py`:

```python
from importlib import resources


def test_signal_v2_migration_adds_expected_feature_columns():
    sql_text = resources.files("altcoin_trend.migrations").joinpath("006_signal_v2_fields.sql").read_text()
    expected_columns = [
        "volume_ratio_1h",
        "volume_impulse_score",
        "return_24h_rank",
        "return_7d_rank",
        "continuation_grade",
        "ignition_grade",
        "signal_priority",
        "risk_flags",
        "chase_risk_score",
        "actionability_score",
        "cross_exchange_confirmed",
    ]

    for column in expected_columns:
        assert f"ADD COLUMN IF NOT EXISTS {column}" in sql_text
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
pytest tests/test_db_migrations.py::test_signal_v2_migration_adds_expected_feature_columns -v
```

Expected: FAIL because `006_signal_v2_fields.sql` does not exist.

- [ ] **Step 3: Add the migration files**

Create identical content in `sql/006_signal_v2_fields.sql` and `src/altcoin_trend/migrations/006_signal_v2_fields.sql`:

```sql
ALTER TABLE alt_signal.feature_snapshot
    ADD COLUMN IF NOT EXISTS volume_ratio_1h DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS volume_impulse_score DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS return_24h_rank INTEGER,
    ADD COLUMN IF NOT EXISTS return_7d_rank INTEGER,
    ADD COLUMN IF NOT EXISTS continuation_grade TEXT,
    ADD COLUMN IF NOT EXISTS ignition_grade TEXT,
    ADD COLUMN IF NOT EXISTS signal_priority INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS risk_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS chase_risk_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS actionability_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cross_exchange_confirmed BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE alt_signal.alert_events
    DROP CONSTRAINT IF EXISTS alert_events_alert_type_check;

ALTER TABLE alt_signal.alert_events
    ADD CONSTRAINT alert_events_alert_type_check
    CHECK (alert_type IN (
        'strong_trend',
        'watchlist_enter',
        'breakout_confirmed',
        'risk_downgrade',
        'explosive_move_early',
        'continuation_confirmed',
        'ignition_detected',
        'ignition_extreme',
        'exhaustion_risk'
    ));
```

- [ ] **Step 4: Run migration tests**

Run:

```bash
pytest tests/test_db_migrations.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sql/006_signal_v2_fields.sql src/altcoin_trend/migrations/006_signal_v2_fields.sql tests/test_db_migrations.py
git commit -m "feat: add signal v2 schema fields"
```

## Task 2: Add Pure Signal v2 Helper Module

**Files:**
- Create: `src/altcoin_trend/signals/v2.py`
- Create: `tests/test_signal_v2.py`

- [ ] **Step 1: Write failing tests for volume and top-rank helpers**

Create `tests/test_signal_v2.py` with:

```python
from altcoin_trend.signals.v2 import (
    compute_volume_impulse_score,
    is_top_24h,
    is_top_7d,
    ratio_score,
)


def test_ratio_score_uses_log_scale_and_clamps():
    assert ratio_score(None, full_at=5.0) == 0.0
    assert ratio_score(1.0, full_at=5.0) == 0.0
    assert ratio_score(5.0, full_at=5.0) == 100.0
    assert ratio_score(25.0, full_at=5.0) == 100.0


def test_compute_volume_impulse_score_weights_1h_4h_24h_and_breakout():
    row = {
        "volume_ratio_1h": 6.0,
        "volume_ratio_4h": 5.0,
        "volume_ratio_24h": 4.0,
        "breakout_20d": True,
    }

    assert compute_volume_impulse_score(row) == 100.0


def test_top_return_helpers_accept_rank_or_percentile():
    assert is_top_24h({"return_24h_rank": 3, "return_24h_percentile": 0.10}, max_rank=3, min_percentile=0.94)
    assert is_top_24h({"return_24h_rank": 9, "return_24h_percentile": 0.95}, max_rank=3, min_percentile=0.94)
    assert not is_top_24h({"return_24h_rank": 4, "return_24h_percentile": 0.93}, max_rank=3, min_percentile=0.94)
    assert is_top_7d({"return_7d_rank": 5, "return_7d_percentile": 0.10}, max_rank=5, min_percentile=0.84)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
pytest tests/test_signal_v2.py -v
```

Expected: FAIL with `ModuleNotFoundError` for `altcoin_trend.signals.v2`.

- [ ] **Step 3: Create helper module with volume and rank helpers**

Create `src/altcoin_trend/signals/v2.py`:

```python
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


CONTINUATION_A = "A"
CONTINUATION_B = "B"
IGNITION_EXTREME = "EXTREME"
IGNITION_A = "A"
IGNITION_B = "B"


@dataclass(frozen=True)
class SignalV2Result:
    continuation_grade: str | None
    ignition_grade: str | None
    signal_priority: int
    risk_flags: tuple[str, ...]
    chase_risk_score: float
    actionability_score: float


def get_value(row: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def float_value(row: Mapping[str, Any] | Any, key: str, default: float | None = None) -> float | None:
    value = get_value(row, key, default)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_items(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        normalized = value.strip()
        return (normalized,) if normalized else ()
    if isinstance(value, Sequence):
        return tuple(str(item).strip() for item in value if str(item).strip())
    normalized = str(value).strip()
    return (normalized,) if normalized else ()


def has_veto(row: Mapping[str, Any] | Any) -> bool:
    return bool(normalize_items(get_value(row, "veto_reason_codes", None)))


def ratio_score(value: float | None, full_at: float) -> float:
    if value is None or value <= 1.0:
        return 0.0
    return max(0.0, min(100.0, math.log(float(value)) / math.log(full_at) * 100.0))


def compute_volume_impulse_score(row: Mapping[str, Any] | Any) -> float:
    volume_ratio_1h = float_value(row, "volume_ratio_1h", 1.0)
    volume_ratio_4h = float_value(row, "volume_ratio_4h", 1.0)
    volume_ratio_24h = float_value(row, "volume_ratio_24h", 1.0)
    score = (
        0.40 * ratio_score(volume_ratio_1h, full_at=6.0)
        + 0.35 * ratio_score(volume_ratio_4h, full_at=5.0)
        + 0.25 * ratio_score(volume_ratio_24h, full_at=4.0)
    )
    if bool(get_value(row, "breakout_20d", False)):
        score += 10.0
    return round(min(100.0, score), 4)


def _top_by_rank_or_percentile(row: Mapping[str, Any] | Any, rank_key: str, pct_key: str, max_rank: int, min_percentile: float) -> bool:
    rank = float_value(row, rank_key)
    percentile = float_value(row, pct_key)
    rank_ok = rank is not None and rank <= max_rank
    percentile_ok = percentile is not None and percentile >= min_percentile
    return rank_ok or percentile_ok


def is_top_24h(row: Mapping[str, Any] | Any, max_rank: int, min_percentile: float) -> bool:
    return _top_by_rank_or_percentile(row, "return_24h_rank", "return_24h_percentile", max_rank, min_percentile)


def is_top_7d(row: Mapping[str, Any] | Any, max_rank: int, min_percentile: float) -> bool:
    return _top_by_rank_or_percentile(row, "return_7d_rank", "return_7d_percentile", max_rank, min_percentile)
```

- [ ] **Step 4: Run helper tests**

Run:

```bash
pytest tests/test_signal_v2.py -v
```

Expected: PASS.

- [ ] **Step 5: Add failing tests for continuation and ignition grades**

Append to `tests/test_signal_v2.py`:

```python
from altcoin_trend.signals.v2 import continuation_grade, ignition_grade


def _continuation_row(**overrides):
    row = {
        "return_1h_pct": 6.1,
        "return_4h_pct": 10.1,
        "return_24h_pct": 12.1,
        "volume_ratio_24h": 5.1,
        "return_24h_rank": 3,
        "return_24h_percentile": 0.80,
        "return_7d_rank": 5,
        "return_7d_percentile": 0.80,
        "relative_strength_score": 86.0,
        "derivatives_score": 46.0,
        "volume_breakout_score": 51.0,
        "volume_impulse_score": 40.0,
        "quality_score": 100.0,
        "veto_reason_codes": [],
    }
    row.update(overrides)
    return row


def _ignition_row(**overrides):
    row = {
        "return_1h_pct": 8.1,
        "return_24h_pct": 25.1,
        "return_24h_rank": 3,
        "return_24h_percentile": 0.80,
        "relative_strength_score": 86.0,
        "quality_score": 100.0,
        "volume_ratio_24h": 1.9,
        "volume_impulse_score": 20.0,
        "volume_breakout_score": 20.0,
        "derivatives_score": 30.0,
        "veto_reason_codes": [],
    }
    row.update(overrides)
    return row


def test_continuation_grade_splits_a_and_b_and_respects_veto():
    assert continuation_grade(_continuation_row()) == "A"
    assert continuation_grade(_continuation_row(derivatives_score=44.9)) == "B"
    assert continuation_grade(_continuation_row(return_1h_pct=5.9)) is None
    assert continuation_grade(_continuation_row(veto_reason_codes=["risk"])) is None


def test_ignition_grade_orders_extreme_before_a_before_b():
    assert ignition_grade(_ignition_row()) == "B"
    assert ignition_grade(
        _ignition_row(
            return_1h_pct=10.1,
            return_24h_pct=35.1,
            relative_strength_score=91.0,
            quality_score=86.0,
            volume_impulse_score=46.0,
            derivatives_score=35.0,
        )
    ) == "A"
    assert ignition_grade(
        _ignition_row(
            return_1h_pct=20.1,
            return_24h_pct=70.1,
            return_24h_percentile=0.95,
            relative_strength_score=91.0,
            volume_ratio_24h=1.6,
            derivatives_score=25.0,
        )
    ) == "EXTREME"
```

- [ ] **Step 6: Run the grade tests to verify they fail**

Run:

```bash
pytest tests/test_signal_v2.py::test_continuation_grade_splits_a_and_b_and_respects_veto tests/test_signal_v2.py::test_ignition_grade_orders_extreme_before_a_before_b -v
```

Expected: FAIL because `continuation_grade` and `ignition_grade` are not defined.

- [ ] **Step 7: Add grade functions**

Append to `src/altcoin_trend/signals/v2.py`:

```python
def _required_values(row: Mapping[str, Any] | Any, keys: tuple[str, ...]) -> dict[str, float] | None:
    values = {key: float_value(row, key) for key in keys}
    if any(value is None for value in values.values()):
        return None
    return {key: float(value) for key, value in values.items() if value is not None}


def continuation_grade(row: Mapping[str, Any] | Any) -> str | None:
    if has_veto(row):
        return None
    values = _required_values(
        row,
        (
            "return_1h_pct",
            "return_4h_pct",
            "return_24h_pct",
            "volume_ratio_24h",
            "quality_score",
        ),
    )
    if values is None:
        return None
    base = (
        values["return_1h_pct"] >= 6.0
        and values["return_4h_pct"] >= 10.0
        and values["return_24h_pct"] >= 12.0
        and values["volume_ratio_24h"] >= 5.0
        and is_top_24h(row, max_rank=3, min_percentile=0.94)
        and is_top_7d(row, max_rank=5, min_percentile=0.84)
        and values["quality_score"] >= 80.0
    )
    if not base:
        return None

    relative_strength_score = float_value(row, "relative_strength_score", 0.0) or 0.0
    derivatives_score = float_value(row, "derivatives_score", 0.0) or 0.0
    volume_breakout_score = float_value(row, "volume_breakout_score", 0.0) or 0.0
    volume_impulse_score = float_value(row, "volume_impulse_score", 0.0) or 0.0
    if (
        relative_strength_score >= 85.0
        and derivatives_score >= 45.0
        and (volume_breakout_score >= 50.0 or volume_impulse_score >= 50.0)
    ):
        return CONTINUATION_A
    return CONTINUATION_B


def _volume_confirmed(row: Mapping[str, Any] | Any, ratio_threshold: float, impulse_threshold: float, breakout_threshold: float) -> bool:
    volume_ratio_24h = float_value(row, "volume_ratio_24h", 0.0) or 0.0
    volume_impulse_score = float_value(row, "volume_impulse_score", 0.0) or 0.0
    volume_breakout_score = float_value(row, "volume_breakout_score", 0.0) or 0.0
    return (
        volume_ratio_24h >= ratio_threshold
        or volume_impulse_score >= impulse_threshold
        or volume_breakout_score >= breakout_threshold
    )


def ignition_grade(row: Mapping[str, Any] | Any) -> str | None:
    if has_veto(row):
        return None
    values = _required_values(
        row,
        (
            "return_1h_pct",
            "return_24h_pct",
            "relative_strength_score",
            "quality_score",
            "derivatives_score",
        ),
    )
    if values is None:
        return None

    extreme = (
        values["return_1h_pct"] >= 20.0
        and values["return_24h_pct"] >= 70.0
        and is_top_24h(row, max_rank=3, min_percentile=0.94)
        and values["relative_strength_score"] >= 90.0
        and values["quality_score"] >= 80.0
        and _volume_confirmed(row, ratio_threshold=1.5, impulse_threshold=35.0, breakout_threshold=35.0)
        and values["derivatives_score"] >= 25.0
    )
    if extreme:
        return IGNITION_EXTREME

    grade_a = (
        values["return_1h_pct"] >= 10.0
        and values["return_24h_pct"] >= 35.0
        and is_top_24h(row, max_rank=3, min_percentile=0.94)
        and values["relative_strength_score"] >= 90.0
        and values["quality_score"] >= 85.0
        and _volume_confirmed(row, ratio_threshold=2.2, impulse_threshold=45.0, breakout_threshold=45.0)
        and values["derivatives_score"] >= 35.0
    )
    if grade_a:
        return IGNITION_A

    grade_b = (
        values["return_1h_pct"] >= 8.0
        and values["return_24h_pct"] >= 25.0
        and is_top_24h(row, max_rank=3, min_percentile=0.92)
        and values["relative_strength_score"] >= 85.0
        and values["quality_score"] >= 80.0
        and _volume_confirmed(row, ratio_threshold=1.8, impulse_threshold=45.0, breakout_threshold=35.0)
        and values["derivatives_score"] >= 30.0
    )
    if grade_b:
        return IGNITION_B
    return None
```

- [ ] **Step 8: Run grade tests**

Run:

```bash
pytest tests/test_signal_v2.py -v
```

Expected: PASS.

- [ ] **Step 9: Add failing tests for risk, priority, and actionability**

Append to `tests/test_signal_v2.py`:

```python
from altcoin_trend.signals.v2 import (
    compute_actionability_score,
    compute_chase_risk_score,
    compute_risk_flags,
    evaluate_signal_v2,
)


def test_chase_risk_and_flags_mark_extreme_crowded_moves():
    row = _ignition_row(
        return_1h_pct=26.0,
        return_24h_pct=110.0,
        funding_zscore=2.6,
        taker_buy_sell_ratio=2.6,
        oi_delta_1h=-1.0,
        ignition_grade="EXTREME",
    )

    assert compute_chase_risk_score(row) == 100.0
    assert compute_risk_flags(row, ignition_grade="EXTREME", chase_risk_score=100.0) == (
        "EXTREME_MOVE",
        "CHASE_RISK",
        "FUNDING_OVERHEAT",
        "PRICE_UP_OI_DOWN",
        "TAKER_CROWDING",
        "EXTENDED_1H",
        "EXTENDED_24H",
    )


def test_actionability_rewards_grade_confirmations_and_penalizes_risk():
    low_risk = _continuation_row(continuation_grade="A", cross_exchange_confirmed=True, volume_impulse_score=60.0)
    high_risk = dict(low_risk, chase_risk_score=80.0, risk_flags=["PRICE_UP_OI_DOWN"])

    assert compute_actionability_score(low_risk, continuation_grade="A", ignition_grade=None, risk_flags=(), chase_risk_score=0.0) > 60.0
    assert compute_actionability_score(high_risk, continuation_grade="A", ignition_grade=None, risk_flags=("PRICE_UP_OI_DOWN",), chase_risk_score=80.0) < 60.0


def test_evaluate_signal_v2_returns_complete_result():
    result = evaluate_signal_v2(_continuation_row())

    assert result.continuation_grade == "A"
    assert result.ignition_grade is None
    assert result.signal_priority == 3
    assert result.actionability_score > 0.0
```

- [ ] **Step 10: Run risk/actionability tests to verify they fail**

Run:

```bash
pytest tests/test_signal_v2.py::test_chase_risk_and_flags_mark_extreme_crowded_moves tests/test_signal_v2.py::test_actionability_rewards_grade_confirmations_and_penalizes_risk tests/test_signal_v2.py::test_evaluate_signal_v2_returns_complete_result -v
```

Expected: FAIL because these functions are not defined.

- [ ] **Step 11: Add risk/actionability functions**

Append to `src/altcoin_trend/signals/v2.py`:

```python
def signal_priority_for(continuation: str | None, ignition: str | None) -> int:
    if continuation == CONTINUATION_A or ignition == IGNITION_EXTREME:
        return 3
    if continuation == CONTINUATION_B or ignition == IGNITION_A:
        return 2
    if ignition == IGNITION_B:
        return 1
    return 0


def compute_chase_risk_score(row: Mapping[str, Any] | Any) -> float:
    return_1h_pct = float_value(row, "return_1h_pct", 0.0) or 0.0
    return_24h_pct = float_value(row, "return_24h_pct", 0.0) or 0.0
    funding_zscore = float_value(row, "funding_zscore", 0.0) or 0.0
    taker_ratio = float_value(row, "taker_buy_sell_ratio", 1.0) or 1.0
    score = 0.0
    if return_1h_pct >= 15.0:
        score += 20.0
    if return_1h_pct >= 25.0:
        score += 20.0
    if return_24h_pct >= 60.0:
        score += 20.0
    if return_24h_pct >= 100.0:
        score += 20.0
    if funding_zscore >= 2.0:
        score += 10.0
    if taker_ratio >= 2.2:
        score += 10.0
    return min(100.0, score)


def compute_risk_flags(row: Mapping[str, Any] | Any, ignition_grade: str | None, chase_risk_score: float) -> tuple[str, ...]:
    flags: list[str] = []
    return_1h_pct = float_value(row, "return_1h_pct", 0.0) or 0.0
    return_24h_pct = float_value(row, "return_24h_pct", 0.0) or 0.0
    funding_zscore = float_value(row, "funding_zscore", 0.0) or 0.0
    oi_delta_1h = float_value(row, "oi_delta_1h", 0.0) or 0.0
    taker_ratio = float_value(row, "taker_buy_sell_ratio", 1.0) or 1.0

    if ignition_grade == IGNITION_EXTREME:
        flags.append("EXTREME_MOVE")
    if chase_risk_score >= 60.0:
        flags.append("CHASE_RISK")
    if funding_zscore >= 2.5:
        flags.append("FUNDING_OVERHEAT")
    if oi_delta_1h < 0.0 and return_1h_pct >= 8.0:
        flags.append("PRICE_UP_OI_DOWN")
    if taker_ratio >= 2.5:
        flags.append("TAKER_CROWDING")
    if return_1h_pct >= 25.0:
        flags.append("EXTENDED_1H")
    if return_24h_pct >= 100.0:
        flags.append("EXTENDED_24H")
    return tuple(flags)


def compute_actionability_score(
    row: Mapping[str, Any] | Any,
    continuation_grade: str | None,
    ignition_grade: str | None,
    risk_flags: tuple[str, ...],
    chase_risk_score: float,
) -> float:
    score = 0.0
    if continuation_grade == CONTINUATION_A:
        score += 35.0
    elif continuation_grade == CONTINUATION_B:
        score += 25.0

    if ignition_grade == IGNITION_A:
        score += 25.0
    elif ignition_grade == IGNITION_B:
        score += 15.0
    elif ignition_grade == IGNITION_EXTREME:
        score += 20.0

    relative_strength_score = float_value(row, "relative_strength_score", 0.0) or 0.0
    volume_impulse_score = float_value(row, "volume_impulse_score", 0.0) or 0.0
    quality_score = float_value(row, "quality_score", 0.0) or 0.0
    score += min(15.0, relative_strength_score * 0.15)
    score += min(15.0, volume_impulse_score * 0.15)
    score += min(10.0, quality_score * 0.10)

    if bool(get_value(row, "cross_exchange_confirmed", False)):
        score += 8.0

    if chase_risk_score >= 80.0:
        score -= 25.0
    elif chase_risk_score >= 60.0:
        score -= 15.0
    elif chase_risk_score >= 40.0:
        score -= 8.0

    if "PRICE_UP_OI_DOWN" in risk_flags:
        score -= 10.0
    if "FUNDING_OVERHEAT" in risk_flags and (float_value(row, "return_1h_pct", 0.0) or 0.0) >= 20.0:
        score -= 10.0

    return round(max(0.0, min(100.0, score)), 4)


def evaluate_signal_v2(row: Mapping[str, Any] | Any) -> SignalV2Result:
    continuation = continuation_grade(row)
    ignition = ignition_grade(row)
    chase_risk = compute_chase_risk_score(row)
    risks = compute_risk_flags(row, ignition_grade=ignition, chase_risk_score=chase_risk)
    actionability = compute_actionability_score(
        row,
        continuation_grade=continuation,
        ignition_grade=ignition,
        risk_flags=risks,
        chase_risk_score=chase_risk,
    )
    return SignalV2Result(
        continuation_grade=continuation,
        ignition_grade=ignition,
        signal_priority=signal_priority_for(continuation, ignition),
        risk_flags=risks,
        chase_risk_score=chase_risk,
        actionability_score=actionability,
    )
```

- [ ] **Step 12: Run all signal v2 tests**

Run:

```bash
pytest tests/test_signal_v2.py -v
```

Expected: PASS.

- [ ] **Step 13: Commit**

```bash
git add src/altcoin_trend/signals/v2.py tests/test_signal_v2.py
git commit -m "feat: add signal v2 grading helpers"
```

## Task 3: Wire v2 Fields Into Snapshot Building

**Files:**
- Modify: `src/altcoin_trend/scheduler.py`
- Modify: `tests/test_scheduler.py`
- Modify: `tests/test_trade_candidate.py`

- [ ] **Step 1: Write failing scheduler tests for ranks and v2 fields**

Add this test to `tests/test_scheduler.py`:

```python
def test_build_snapshot_rows_populates_signal_v2_fields_and_keeps_trade_candidate_compatible():
    snapshot_ts = datetime(2026, 2, 1, tzinfo=timezone.utc)
    rows = []
    for hour in range(24 * 31):
        ts = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=hour)
        btc_close = 100.0 + hour * 0.01
        eth_close = 100.0 + hour * 0.01
        fast_close = 100.0 + hour * 0.01
        rave_close = 100.0 + hour * 0.01
        if hour >= 24 * 30 - 24:
            fast_close += (hour - (24 * 30 - 24)) * 0.7
        if hour == 24 * 31 - 1:
            fast_close *= 1.13
            rave_close = 220.0
        elif hour > 24 * 31 - 25:
            rave_close = 125.0

        for asset_id, symbol, close, quote_volume in (
            (1, "BTCUSDT", btc_close, 1000.0),
            (2, "ETHUSDT", eth_close, 1000.0),
            (3, "FASTUSDT", fast_close, 10000.0 if hour == 24 * 31 - 1 else 1000.0),
            (4, "RAVEUSDT", rave_close, 2500.0 if hour == 24 * 31 - 1 else 1000.0),
        ):
            rows.append(
                {
                    "asset_id": asset_id,
                    "exchange": "binance",
                    "symbol": symbol,
                    "base_asset": symbol.removesuffix("USDT"),
                    "ts": ts,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": quote_volume,
                    "quote_volume": quote_volume,
                    "open_interest": 1000.0 + hour,
                    "funding_rate": 0.0001,
                    "taker_buy_quote": quote_volume * 0.56,
                }
            )

    feature_rows, rank_rows = build_snapshot_rows(pd.DataFrame(rows), snapshot_ts)
    by_symbol = {row["symbol"]: row for row in feature_rows}

    assert by_symbol["FASTUSDT"]["return_24h_rank"] is not None
    assert by_symbol["FASTUSDT"]["return_7d_rank"] is not None
    assert by_symbol["FASTUSDT"]["volume_ratio_1h"] is not None
    assert by_symbol["FASTUSDT"]["volume_impulse_score"] >= 0.0
    assert by_symbol["FASTUSDT"]["continuation_grade"] in {"A", "B"}
    assert by_symbol["FASTUSDT"]["continuation_candidate"] is True
    assert by_symbol["FASTUSDT"]["trade_candidate"] is True

    assert by_symbol["RAVEUSDT"]["ignition_grade"] == "EXTREME"
    assert by_symbol["RAVEUSDT"]["ignition_candidate"] is True
    assert by_symbol["RAVEUSDT"]["trade_candidate"] is False
    assert by_symbol["RAVEUSDT"]["signal_priority"] == 3
    assert "EXTREME_MOVE" in by_symbol["RAVEUSDT"]["risk_flags"]
    assert by_symbol["RAVEUSDT"]["actionability_score"] >= 0.0

    rank_payload = next(row for row in rank_rows if row["symbol"] == "RAVEUSDT" and row["rank_scope"] == "all")["payload"]
    assert rank_payload["ignition_grade"] == "EXTREME"
    assert rank_payload["signal_priority"] == 3
    assert "actionability_score" in rank_payload
```

- [ ] **Step 2: Run the scheduler test to verify it fails**

Run:

```bash
pytest tests/test_scheduler.py::test_build_snapshot_rows_populates_signal_v2_fields_and_keeps_trade_candidate_compatible -v
```

Expected: FAIL because v2 fields are not populated by `build_snapshot_rows`.

- [ ] **Step 3: Import v2 helpers in scheduler**

Modify the imports in `src/altcoin_trend/scheduler.py`:

```python
from altcoin_trend.signals.v2 import compute_volume_impulse_score, evaluate_signal_v2
```

- [ ] **Step 4: Add `volume_ratio_1h` to higher-timeframe features**

In `_higher_timeframe_features`, include `volume_ratio_1h` in the initial `features` dict:

```python
"volume_ratio_1h": None,
```

In the ordered-data block, after `features["volume_ratio_24h"] = _trailing_volume_ratio_24h(ordered)`, set:

```python
features["volume_ratio_1h"] = features["volume_ratio_24h"]
```

This uses the existing trailing latest-hour versus 24h average-hour calculation as the explicit 1h volume ratio. Keep `volume_ratio_24h` unchanged for compatibility.

- [ ] **Step 5: Replace percentile-only assignment with percentile plus rank**

Rename `_assign_return_percentiles` to `_assign_return_percentiles_and_ranks`, then implement:

```python
def _assign_return_percentiles_and_ranks(feature_rows: list[dict[str, Any]]) -> None:
    if not feature_rows:
        return
    frame = pd.DataFrame(feature_rows)
    for source_column, percentile_column, rank_column in (
        ("return_24h_pct", "return_24h_percentile", "return_24h_rank"),
        ("return_7d_pct", "return_7d_percentile", "return_7d_rank"),
    ):
        if source_column not in frame.columns:
            for row in feature_rows:
                row[percentile_column] = None
                row[rank_column] = None
            continue
        frame[percentile_column] = frame.groupby("exchange")[source_column].rank(pct=True)
        frame[rank_column] = frame.groupby("exchange")[source_column].rank(method="min", ascending=False)
        for index, row in enumerate(feature_rows):
            percentile = frame.iloc[index][percentile_column]
            rank = frame.iloc[index][rank_column]
            row[percentile_column] = None if pd.isna(percentile) else float(percentile)
            row[rank_column] = None if pd.isna(rank) else int(rank)
```

Update the call site:

```python
_assign_return_percentiles_and_ranks(feature_rows)
```

- [ ] **Step 6: Add v2 fields during row finalization**

In the first `feature_rows.append` dict, add initial values:

```python
"volume_impulse_score": 0.0,
"return_24h_rank": None,
"return_7d_rank": None,
"continuation_grade": None,
"ignition_grade": None,
"signal_priority": 0,
"risk_flags": [],
"chase_risk_score": 0.0,
"actionability_score": 0.0,
"cross_exchange_confirmed": False,
```

After rank assignment and before `trade_candidate`, compute volume impulse:

```python
for row in feature_rows:
    row["volume_impulse_score"] = compute_volume_impulse_score(row)
```

Then compute provisional signal results:

```python
for row in feature_rows:
    result = evaluate_signal_v2(row)
    row["continuation_grade"] = result.continuation_grade
    row["ignition_grade"] = result.ignition_grade
    row["signal_priority"] = result.signal_priority
    row["risk_flags"] = list(result.risk_flags)
    row["chase_risk_score"] = result.chase_risk_score
    row["actionability_score"] = result.actionability_score
    row["continuation_candidate"] = result.continuation_grade is not None
    row["ignition_candidate"] = result.ignition_grade is not None
    row["trade_candidate"] = result.continuation_grade is not None
```

- [ ] **Step 7: Add cross-exchange confirmation and recompute actionability**

After provisional grading, add:

```python
trigger_counts: dict[str, int] = {}
for row in feature_rows:
    if row.get("continuation_grade") or row.get("ignition_grade"):
        trigger_counts[str(row["symbol"])] = trigger_counts.get(str(row["symbol"]), 0) + 1

for row in feature_rows:
    row["cross_exchange_confirmed"] = trigger_counts.get(str(row["symbol"]), 0) >= 2
    result = evaluate_signal_v2(row)
    row["risk_flags"] = list(result.risk_flags)
    row["chase_risk_score"] = result.chase_risk_score
    row["actionability_score"] = result.actionability_score
```

- [ ] **Step 8: Update tier override by grade**

Replace the ignition boolean override block with:

```python
if row["ignition_grade"] in {"A", "B"}:
    row["tier"] = max_tier(row["tier"], "watchlist")
if row["ignition_grade"] == "EXTREME":
    row["tier"] = max_tier(row["tier"], "strong")
```

- [ ] **Step 9: Add v2 fields to rank payload**

In `_rank_rows_for_scope`, expand `payload`:

```python
"continuation_grade": row.get("continuation_grade"),
"ignition_grade": row.get("ignition_grade"),
"signal_priority": int(row.get("signal_priority", 0)),
"risk_flags": list(row.get("risk_flags", [])),
"chase_risk_score": float(row.get("chase_risk_score", 0.0)),
"actionability_score": float(row.get("actionability_score", 0.0)),
"cross_exchange_confirmed": bool(row.get("cross_exchange_confirmed", False)),
```

- [ ] **Step 10: Update insert filtering**

In `write_run_once_snapshots`, keep excluding only non-table fields:

```python
if key not in {"base_asset", "tier", "primary_reason"}
```

This allows all new v2 feature fields to be inserted after the migration.

- [ ] **Step 11: Run scheduler and trade candidate tests**

Run:

```bash
pytest tests/test_scheduler.py tests/test_trade_candidate.py tests/test_signal_v2.py -v
```

Expected: PASS.

- [ ] **Step 12: Commit**

```bash
git add src/altcoin_trend/scheduler.py tests/test_scheduler.py tests/test_trade_candidate.py
git commit -m "feat: write signal v2 snapshot fields"
```

## Task 4: Add Opportunity Ranking Queries and CLI Output

**Files:**
- Modify: `src/altcoin_trend/scheduler.py`
- Modify: `src/altcoin_trend/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add failing CLI test for opportunities command**

In `tests/test_cli.py`, add:

```python
def test_opportunities_command_prints_actionability_rows(monkeypatch):
    from typer.testing import CliRunner
    from altcoin_trend.cli import app

    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: object())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.load_opportunity_rows",
        lambda engine, limit: [
            {
                "exchange": "binance",
                "symbol": "RAVEUSDT",
                "actionability_score": 68.5,
                "signal_priority": 3,
                "continuation_grade": None,
                "ignition_grade": "EXTREME",
                "chase_risk_score": 80.0,
                "final_score": 63.0,
            }
        ],
    )

    result = CliRunner().invoke(app, ["opportunities", "--limit", "5"])

    assert result.exit_code == 0
    assert "RAVEUSDT" in result.output
    assert "actionability=68.5" in result.output
    assert "ignition=EXTREME" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_cli.py::test_opportunities_command_prints_actionability_rows -v
```

Expected: FAIL because `load_opportunity_rows` and the command do not exist.

- [ ] **Step 3: Add `load_opportunity_rows`**

Add to `src/altcoin_trend/scheduler.py`:

```python
def load_opportunity_rows(engine: Engine, limit: int = 30) -> list[dict[str, Any]]:
    statement = text(
        """
        SELECT
            fs.ts,
            fs.asset_id,
            fs.exchange,
            fs.symbol,
            a.base_asset,
            fs.close,
            fs.final_score,
            fs.tier,
            fs.continuation_grade,
            fs.ignition_grade,
            fs.signal_priority,
            fs.risk_flags,
            fs.chase_risk_score,
            fs.actionability_score,
            fs.cross_exchange_confirmed,
            fs.return_1h_pct,
            fs.return_4h_pct,
            fs.return_24h_pct,
            fs.volume_impulse_score
        FROM alt_signal.feature_snapshot AS fs
        JOIN alt_core.asset_master AS a ON a.asset_id = fs.asset_id
        WHERE fs.ts = (
              SELECT MAX(ts)
              FROM alt_signal.feature_snapshot
          )
          AND fs.signal_priority > 0
        ORDER BY fs.actionability_score DESC, fs.signal_priority DESC, fs.final_score DESC
        LIMIT :limit
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, {"limit": limit})
        return [dict(row) for row in result.mappings().all()]
```

- [ ] **Step 4: Add CLI command**

Import `load_opportunity_rows` in `src/altcoin_trend/cli.py`, then add:

```python
@app.command("opportunities")
def opportunities(limit: int = typer.Option(30, "--limit", min=1)) -> None:
    settings = load_settings()
    engine = build_engine(settings)
    rows = load_opportunity_rows(engine, limit=limit)
    if not rows:
        typer.echo("No opportunities found in latest snapshot")
        return
    typer.echo(f"Opportunities limit={limit}")
    for index, row in enumerate(rows, start=1):
        typer.echo(
            f"{index}. {row['exchange']}:{row['symbol']} "
            f"actionability={float(row['actionability_score']):.1f} "
            f"priority={int(row['signal_priority'])} "
            f"continuation={row.get('continuation_grade') or '-'} "
            f"ignition={row.get('ignition_grade') or '-'} "
            f"chase_risk={float(row['chase_risk_score']):.1f} "
            f"score={float(row['final_score']):.1f}"
        )
```

- [ ] **Step 5: Run CLI test**

Run:

```bash
pytest tests/test_cli.py::test_opportunities_command_prints_actionability_rows -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/altcoin_trend/scheduler.py src/altcoin_trend/cli.py tests/test_cli.py
git commit -m "feat: add opportunity ranking output"
```

## Task 5: Add v2 Alert Types, Priority, and Messages

**Files:**
- Modify: `src/altcoin_trend/signals/alerts.py`
- Modify: `tests/test_alerts.py`

- [ ] **Step 1: Write failing tests for v2 event classification**

Add to `tests/test_alerts.py`:

```python
def test_build_alert_event_rows_creates_ignition_extreme_event():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rank_row = {
        "asset_id": 21,
        "exchange": "binance",
        "symbol": "RAVEUSDT",
        "tier": "strong",
        "final_score": 63.0,
        "trend_score": 67.0,
        "volume_breakout_score": 35.0,
        "volume_impulse_score": 48.0,
        "relative_strength_score": 92.0,
        "derivatives_score": 31.0,
        "quality_score": 100.0,
        "return_1h_pct": 22.0,
        "return_24h_pct": 80.0,
        "continuation_grade": None,
        "ignition_grade": "EXTREME",
        "signal_priority": 3,
        "risk_flags": ["EXTREME_MOVE", "CHASE_RISK"],
        "chase_risk_score": 80.0,
        "actionability_score": 55.0,
        "cross_exchange_confirmed": True,
        "veto_reason_codes": [],
    }

    events = build_alert_event_rows([rank_row], recent_events=[], now=now, cooldown_seconds=3600)

    assert events[0]["alert_type"] == "ignition_extreme"
    assert "[IGNITION_EXTREME] RAVEUSDT" in events[0]["message"]
    assert events[0]["payload"]["priority"] == "P1"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_alerts.py::test_build_alert_event_rows_creates_ignition_extreme_event -v
```

Expected: FAIL because v2 alert events are not created.

- [ ] **Step 3: Add alert priority helpers**

Add to `src/altcoin_trend/signals/alerts.py`:

```python
def _alert_priority_for_type(alert_type: str) -> str:
    if alert_type in {"continuation_confirmed", "ignition_extreme"}:
        return "P1"
    if alert_type in {"ignition_detected", "exhaustion_risk"}:
        return "P2"
    return "P3"


def _cooldown_for_priority(priority: str, default_seconds: int) -> int:
    if priority == "P1":
        return min(default_seconds, 3600)
    if priority == "P2":
        return max(7200, min(default_seconds, 7200))
    return max(default_seconds, 14400)


def _v2_alert_type(row: Mapping[str, Any] | Any) -> str | None:
    continuation_grade = _get(row, "continuation_grade", None)
    ignition_grade = _get(row, "ignition_grade", None)
    risk_flags = set(_normalize_items(_get(row, "risk_flags", None)))
    try:
        chase_risk_score = float(_get(row, "chase_risk_score", 0.0))
    except (TypeError, ValueError):
        chase_risk_score = 0.0

    if ignition_grade == "EXTREME":
        return "ignition_extreme"
    if continuation_grade in {"A", "B"}:
        return "continuation_confirmed"
    if ignition_grade in {"A", "B"}:
        return "ignition_detected"
    if chase_risk_score >= 80.0 or {"FUNDING_OVERHEAT", "TAKER_CROWDING"} & risk_flags:
        return "exhaustion_risk"
    return None
```

- [ ] **Step 4: Add v2 message builder**

Add:

```python
def build_signal_v2_alert_message(row: Mapping[str, Any] | Any, alert_type: str) -> str:
    symbol = str(_get(row, "symbol", "unknown"))
    continuation_grade = _get(row, "continuation_grade", None)
    ignition_grade = _get(row, "ignition_grade", None)
    if alert_type == "ignition_extreme":
        header = f"[IGNITION_EXTREME] {symbol}"
    elif alert_type == "ignition_detected":
        header = f"[IGNITION_{ignition_grade}] {symbol}"
    elif alert_type == "continuation_confirmed":
        header = f"[CONTINUATION_{continuation_grade}] {symbol}"
    else:
        header = f"[EXHAUSTION_RISK] {symbol}"

    def display(key: str) -> Any:
        value = _get(row, key, None)
        return "n/a" if value is None else value

    risks = _normalize_items(_get(row, "risk_flags", None))
    return "\n".join(
        [
            header,
            f"1h {display('return_1h_pct')} | 24h {display('return_24h_pct')}",
            f"RS {display('relative_strength_score')} | Vol impulse {display('volume_impulse_score')} | Deriv {display('derivatives_score')}",
            f"Cross-exchange: {'yes' if bool(_get(row, 'cross_exchange_confirmed', False)) else 'no'}",
            f"Chase risk: {display('chase_risk_score')}",
            f"Actionability: {display('actionability_score')}",
            f"Risks: {', '.join(risks) if risks else 'none'}",
        ]
    )
```

- [ ] **Step 5: Create v2 events before legacy transition events**

At the top of the loop in `build_alert_event_rows`, after `previous_tier`, insert:

```python
v2_alert_type = _v2_alert_type(row)
if v2_alert_type is not None:
    priority = _alert_priority_for_type(v2_alert_type)
    effective_cooldown = _cooldown_for_priority(priority, cooldown_seconds)
    recent_event = events_by_key.get((asset_id, v2_alert_type))
    if recent_event is None or not _event_recent_enough(recent_event, current_time, effective_cooldown):
        alert_rows.append(
            {
                "ts": current_time,
                "asset_id": asset_id,
                "symbol": str(_get(row, "symbol")),
                "alert_type": v2_alert_type,
                "final_score": float(_get(row, "final_score", 0.0)),
                "message": build_signal_v2_alert_message(row, v2_alert_type),
                "payload": {
                    "exchange": _get(row, "exchange", "unknown"),
                    "current_tier": current_tier,
                    "previous_tier": previous_tier,
                    "rank": _get(row, "rank", None),
                    "priority": priority,
                    "continuation_grade": _get(row, "continuation_grade", None),
                    "ignition_grade": _get(row, "ignition_grade", None),
                    "signal_priority": _get(row, "signal_priority", 0),
                    "actionability_score": _get(row, "actionability_score", 0.0),
                    "chase_risk_score": _get(row, "chase_risk_score", 0.0),
                    "risk_flags": list(_normalize_items(_get(row, "risk_flags", None))),
                    "cross_exchange_confirmed": bool(_get(row, "cross_exchange_confirmed", False)),
                },
                "delivery_status": "pending",
                "delivery_error": None,
            }
        )
    continue
```

This `continue` prefers v2 events over duplicate legacy transition events for the same row.

- [ ] **Step 6: Run alert tests**

Run:

```bash
pytest tests/test_alerts.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/altcoin_trend/signals/alerts.py tests/test_alerts.py
git commit -m "feat: add signal v2 alert events"
```

## Task 6: Add Symbol-Level Cross-Exchange Alert Aggregation

**Files:**
- Modify: `src/altcoin_trend/signals/alerts.py`
- Modify: `tests/test_alerts.py`

- [ ] **Step 1: Write failing dedupe test**

Add to `tests/test_alerts.py`:

```python
def test_build_alert_event_rows_dedupes_cross_exchange_ignition_by_symbol():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base = {
        "symbol": "RAVEUSDT",
        "tier": "strong",
        "final_score": 70.0,
        "trend_score": 60.0,
        "volume_breakout_score": 40.0,
        "volume_impulse_score": 50.0,
        "relative_strength_score": 95.0,
        "derivatives_score": 35.0,
        "quality_score": 100.0,
        "return_1h_pct": 24.0,
        "return_24h_pct": 90.0,
        "continuation_grade": None,
        "ignition_grade": "EXTREME",
        "signal_priority": 3,
        "risk_flags": ["EXTREME_MOVE"],
        "chase_risk_score": 80.0,
        "actionability_score": 60.0,
        "cross_exchange_confirmed": True,
        "veto_reason_codes": [],
    }
    rows = [
        dict(base, asset_id=101, exchange="binance"),
        dict(base, asset_id=202, exchange="bybit", actionability_score=65.0),
    ]

    events = build_alert_event_rows(rows, recent_events=[], now=now, cooldown_seconds=3600)

    assert len([event for event in events if event["alert_type"] == "ignition_extreme"]) == 1
    event = events[0]
    assert event["asset_id"] == 202
    assert event["payload"]["per_exchange_signals"] == {
        "binance": "IGNITION_EXTREME",
        "bybit": "IGNITION_EXTREME",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_alerts.py::test_build_alert_event_rows_dedupes_cross_exchange_ignition_by_symbol -v
```

Expected: FAIL because current builder emits one event per asset.

- [ ] **Step 3: Add symbol grouping helper**

Add to `src/altcoin_trend/signals/alerts.py`:

```python
def _signal_family(alert_type: str) -> str:
    if alert_type == "ignition_extreme":
        return "ignition"
    if alert_type == "ignition_detected":
        return "ignition"
    if alert_type == "continuation_confirmed":
        return "continuation"
    return alert_type


def _exchange_signal_label(row: Mapping[str, Any]) -> str:
    continuation = _get(row, "continuation_grade", None)
    ignition = _get(row, "ignition_grade", None)
    if ignition:
        return f"IGNITION_{ignition}"
    if continuation:
        return f"CONTINUATION_{continuation}"
    return "NONE"
```

- [ ] **Step 4: Deduplicate v2 events by symbol and family**

At the start of `build_alert_event_rows`, before the main loop, add:

```python
best_v2_by_symbol_family: dict[tuple[str, str], Mapping[str, Any]] = {}
per_exchange_by_symbol_family: dict[tuple[str, str], dict[str, str]] = {}
for candidate_row in rank_rows:
    candidate_type = _v2_alert_type(candidate_row)
    if candidate_type is None:
        continue
    symbol = str(_get(candidate_row, "symbol"))
    family = _signal_family(candidate_type)
    key = (symbol, family)
    exchange = str(_get(candidate_row, "exchange", "unknown"))
    per_exchange_by_symbol_family.setdefault(key, {})[exchange] = _exchange_signal_label(candidate_row)
    current_best = best_v2_by_symbol_family.get(key)
    current_score = float(_get(candidate_row, "actionability_score", 0.0))
    best_score = float(_get(current_best, "actionability_score", -1.0)) if current_best is not None else -1.0
    if current_best is None or current_score > best_score:
        best_v2_by_symbol_family[key] = candidate_row
```

Inside the loop, before creating a v2 event, skip non-best rows:

```python
if v2_alert_type is not None:
    symbol = str(_get(row, "symbol"))
    family = _signal_family(v2_alert_type)
    if best_v2_by_symbol_family.get((symbol, family)) is not row:
        continue
```

When building payload, add:

```python
"per_exchange_signals": per_exchange_by_symbol_family.get((str(_get(row, "symbol")), _signal_family(v2_alert_type)), {}),
```

- [ ] **Step 5: Run alert tests**

Run:

```bash
pytest tests/test_alerts.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/altcoin_trend/signals/alerts.py tests/test_alerts.py
git commit -m "feat: dedupe signal alerts by symbol"
```

## Task 7: Add v2 Backtest Labels and Grouped Summary

**Files:**
- Modify: `src/altcoin_trend/trade_backtest.py`
- Modify: `tests/test_trade_backtest.py`

- [ ] **Step 1: Write failing tests for MFE, MAE, and hit-before-drawdown**

Add to `tests/test_trade_backtest.py`:

```python
from altcoin_trend.trade_backtest import compute_forward_path_labels


def test_compute_forward_path_labels_detects_target_before_drawdown():
    signal_close = 100.0
    future = pd.DataFrame(
        [
            {"ts": pd.Timestamp("2026-01-01T00:01:00Z"), "high": 104.0, "low": 99.0},
            {"ts": pd.Timestamp("2026-01-01T00:02:00Z"), "high": 111.0, "low": 98.0},
            {"ts": pd.Timestamp("2026-01-01T00:03:00Z"), "high": 112.0, "low": 90.0},
        ]
    )

    labels = compute_forward_path_labels(
        signal_ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        signal_close=signal_close,
        future_rows=future,
    )

    assert labels["mfe_1h_pct"] == 12.0
    assert labels["mae_1h_pct"] == 10.0
    assert labels["hit_10pct_before_drawdown_8pct"] is True
    assert labels["time_to_hit_10pct_minutes"] == 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_trade_backtest.py::test_compute_forward_path_labels_detects_target_before_drawdown -v
```

Expected: FAIL because `compute_forward_path_labels` is not defined.

- [ ] **Step 3: Add forward path label function**

Add to `src/altcoin_trend/trade_backtest.py`:

```python
def compute_forward_path_labels(signal_ts: pd.Timestamp, signal_close: float, future_rows: pd.DataFrame) -> dict[str, Any]:
    if signal_close <= 0 or future_rows.empty:
        return {
            "mfe_1h_pct": 0.0,
            "mfe_4h_pct": 0.0,
            "mfe_24h_pct": 0.0,
            "mae_1h_pct": 0.0,
            "mae_4h_pct": 0.0,
            "mae_24h_pct": 0.0,
            "hit_5pct_before_drawdown_5pct": False,
            "hit_10pct_before_drawdown_8pct": False,
            "time_to_hit_5pct_minutes": None,
            "time_to_hit_10pct_minutes": None,
        }

    frame = future_rows.copy()
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame = frame.sort_values("ts")

    def window_stats(hours: int) -> tuple[float, float]:
        end_ts = signal_ts + pd.Timedelta(hours=hours)
        window = frame[frame["ts"] <= end_ts]
        if window.empty:
            return 0.0, 0.0
        max_high = float(window["high"].max())
        min_low = float(window["low"].min())
        mfe_value = round((max_high / signal_close - 1.0) * 100.0, 6)
        mae_value = round(max(0.0, (1.0 - min_low / signal_close) * 100.0), 6)
        return mfe_value, mae_value

    mfe_1h, mae_1h = window_stats(1)
    mfe_4h, mae_4h = window_stats(4)
    mfe_24h, mae_24h = window_stats(24)

    hit_5 = False
    hit_10 = False
    drawdown_5_first = False
    drawdown_8_first = False
    time_5 = None
    time_10 = None
    for row in frame.to_dict("records"):
        high_return = (float(row["high"]) / signal_close - 1.0) * 100.0
        drawdown = (1.0 - float(row["low"]) / signal_close) * 100.0
        elapsed = (pd.Timestamp(row["ts"]) - signal_ts).total_seconds() / 60.0
        if not hit_5 and not drawdown_5_first:
            if high_return >= 5.0:
                hit_5 = True
                time_5 = elapsed
            elif drawdown >= 5.0:
                drawdown_5_first = True
        if not hit_10 and not drawdown_8_first:
            if high_return >= 10.0:
                hit_10 = True
                time_10 = elapsed
            elif drawdown >= 8.0:
                drawdown_8_first = True

    return {
        "mfe_1h_pct": mfe_1h,
        "mfe_4h_pct": mfe_4h,
        "mfe_24h_pct": mfe_24h,
        "mae_1h_pct": mae_1h,
        "mae_4h_pct": mae_4h,
        "mae_24h_pct": mae_24h,
        "hit_5pct_before_drawdown_5pct": hit_5,
        "hit_10pct_before_drawdown_8pct": hit_10,
        "time_to_hit_5pct_minutes": time_5,
        "time_to_hit_10pct_minutes": time_10,
    }
```

- [ ] **Step 4: Run path label test**

Run:

```bash
pytest tests/test_trade_backtest.py::test_compute_forward_path_labels_detects_target_before_drawdown -v
```

Expected: PASS.

- [ ] **Step 5: Write failing grouped summary test**

Append:

```python
from altcoin_trend.trade_backtest import summarize_signal_v2_groups


def test_summarize_signal_v2_groups_reports_by_grade():
    signals = pd.DataFrame(
        [
            {
                "continuation_grade": "A",
                "ignition_grade": None,
                "mfe_1h_pct": 12.0,
                "mae_1h_pct": 3.0,
                "hit_10pct_before_drawdown_8pct": True,
            },
            {
                "continuation_grade": None,
                "ignition_grade": "B",
                "mfe_1h_pct": 4.0,
                "mae_1h_pct": 9.0,
                "hit_10pct_before_drawdown_8pct": False,
            },
        ]
    )

    summary = summarize_signal_v2_groups(signals)

    assert summary["continuation_A"]["signal_count"] == 1
    assert summary["continuation_A"]["hit_10pct_before_drawdown_8pct_rate"] == 100.0
    assert summary["ignition_B"]["signal_count"] == 1
    assert summary["ignition_B"]["avg_mae_1h_pct"] == 9.0
```

- [ ] **Step 6: Run grouped summary test to verify it fails**

Run:

```bash
pytest tests/test_trade_backtest.py::test_summarize_signal_v2_groups_reports_by_grade -v
```

Expected: FAIL because `summarize_signal_v2_groups` is not defined.

- [ ] **Step 7: Add grouped summary function**

Add:

```python
def _group_mask(frame: pd.DataFrame, group_name: str) -> pd.Series:
    if group_name.startswith("continuation_"):
        grade = group_name.removeprefix("continuation_")
        return frame["continuation_grade"] == grade
    if group_name.startswith("ignition_"):
        grade = group_name.removeprefix("ignition_")
        return frame["ignition_grade"] == grade
    return pd.Series([False] * len(frame), index=frame.index)


def summarize_signal_v2_groups(signals: pd.DataFrame) -> dict[str, dict[str, float | int]]:
    groups = ["continuation_A", "continuation_B", "ignition_A", "ignition_B", "ignition_EXTREME"]
    summary: dict[str, dict[str, float | int]] = {}
    required_columns = {
        "continuation_grade",
        "ignition_grade",
        "mfe_1h_pct",
        "mae_1h_pct",
        "hit_10pct_before_drawdown_8pct",
    }
    if signals.empty or not required_columns.issubset(set(signals.columns)):
        return {
            group: {
                "signal_count": 0,
                "hit_10pct_before_drawdown_8pct_rate": 0.0,
                "avg_mfe_1h_pct": 0.0,
                "avg_mae_1h_pct": 0.0,
            }
            for group in groups
        }
    for group in groups:
        subset = signals[_group_mask(signals, group)]
        if subset.empty:
            summary[group] = {
                "signal_count": 0,
                "hit_10pct_before_drawdown_8pct_rate": 0.0,
                "avg_mfe_1h_pct": 0.0,
                "avg_mae_1h_pct": 0.0,
            }
            continue
        hit_rate = float(subset["hit_10pct_before_drawdown_8pct"].mean()) * 100.0
        summary[group] = {
            "signal_count": int(len(subset)),
            "hit_10pct_before_drawdown_8pct_rate": round(hit_rate, 2),
            "avg_mfe_1h_pct": round(float(subset["mfe_1h_pct"].mean()), 4),
            "avg_mae_1h_pct": round(float(subset["mae_1h_pct"].mean()), 4),
        }
    return summary
```

- [ ] **Step 8: Run backtest tests**

Run:

```bash
pytest tests/test_trade_backtest.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/altcoin_trend/trade_backtest.py tests/test_trade_backtest.py
git commit -m "feat: add signal v2 backtest labels"
```

## Task 8: Add v2 Backtest CLI Command

**Files:**
- Modify: `src/altcoin_trend/cli.py`
- Modify: `src/altcoin_trend/trade_backtest.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI test**

Add to `tests/test_cli.py`:

```python
def test_evaluate_signals_v2_command_prints_group_summary(monkeypatch):
    from typer.testing import CliRunner
    from altcoin_trend.cli import app

    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: object())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.run_signal_v2_backtest",
        lambda engine, exchange, start, end: {
            "continuation_A": {
                "signal_count": 2,
                "hit_10pct_before_drawdown_8pct_rate": 50.0,
                "avg_mfe_1h_pct": 11.0,
                "avg_mae_1h_pct": 4.0,
            }
        },
    )

    result = CliRunner().invoke(
        app,
        ["evaluate-signals-v2", "--from", "2026-01-01", "--to", "2026-01-02", "--exchange", "binance"],
    )

    assert result.exit_code == 0
    assert "continuation_A signals=2 hit10_before_dd8=50.00%" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_cli.py::test_evaluate_signals_v2_command_prints_group_summary -v
```

Expected: FAIL because `run_signal_v2_backtest` and command are not wired.

- [ ] **Step 3: Add minimal `run_signal_v2_backtest` wrapper**

Add to `src/altcoin_trend/trade_backtest.py`:

```python
def run_signal_v2_backtest(engine: Engine, exchange: str, start: datetime, end: datetime) -> dict[str, dict[str, float | int]]:
    start_utc = _coerce_utc_datetime(start)
    end_utc = _coerce_utc_datetime(end)
    market_rows = _fetch_market_rows(engine, exchange=exchange, start=start_utc - timedelta(days=31), end=end_utc + timedelta(hours=1))
    if market_rows.empty:
        return summarize_signal_v2_groups(pd.DataFrame())
    bars = []
    for _, group in market_rows.groupby("asset_id"):
        resampled = resample_market_1m(group, "1h")
        if resampled.empty:
            continue
        latest = group.iloc[-1]
        resampled["asset_id"] = latest["asset_id"]
        resampled["exchange"] = latest["exchange"]
        resampled["symbol"] = latest["symbol"]
        bars.append(resampled)
    if not bars:
        return summarize_signal_v2_groups(pd.DataFrame())
    features = _prepare_feature_frame(pd.concat(bars, ignore_index=True))
    window = features[(features["ts"] >= start_utc) & (features["ts"] < end_utc)].copy()
    if window.empty:
        return summarize_signal_v2_groups(pd.DataFrame())
    return summarize_signal_v2_groups(window)
```

Then update `_prepare_feature_frame` in the same file to compute v2 grades after the existing percentiles:

```python
from altcoin_trend.signals.v2 import compute_volume_impulse_score, evaluate_signal_v2
```

After percentile assignment:

```python
frame["return_24h_rank"] = frame.groupby(["exchange", "ts"])["return_24h_pct"].rank(method="min", ascending=False)
frame["return_7d_rank"] = frame.groupby(["exchange", "ts"])["return_7d_pct"].rank(method="min", ascending=False)
frame["volume_ratio_1h"] = frame["volume_ratio_24h"]
frame["volume_impulse_score"] = [compute_volume_impulse_score(row) for row in frame.to_dict("records")]
results = [evaluate_signal_v2(row) for row in frame.to_dict("records")]
frame["continuation_grade"] = [result.continuation_grade for result in results]
frame["ignition_grade"] = [result.ignition_grade for result in results]
frame["mfe_1h_pct"] = frame["future_max_return_1h"].fillna(0.0) * 100.0
frame["mae_1h_pct"] = 0.0
frame["hit_10pct_before_drawdown_8pct"] = frame["future_max_return_1h"].fillna(0.0) >= 0.10
```

- [ ] **Step 4: Add CLI command**

Import `run_signal_v2_backtest` in `src/altcoin_trend/cli.py`, then add:

```python
@app.command("evaluate-signals-v2")
def evaluate_signals_v2(
    from_ts: str = typer.Option(..., "--from"),
    to_ts: str = typer.Option(..., "--to"),
    exchange: str = typer.Option("binance", "--exchange"),
) -> None:
    start = _parse_iso_datetime(from_ts)
    end = _parse_iso_datetime(to_ts)
    if start >= end:
        raise typer.BadParameter("--from must be earlier than --to")
    settings = load_settings()
    engine = build_engine(settings)
    summary = run_signal_v2_backtest(engine=engine, exchange=exchange, start=start, end=end)
    typer.echo(f"Signal v2 backtest exchange={exchange} from={start.isoformat()} to={end.isoformat()}")
    for group, stats in summary.items():
        typer.echo(
            f"{group} signals={stats['signal_count']} "
            f"hit10_before_dd8={float(stats['hit_10pct_before_drawdown_8pct_rate']):.2f}% "
            f"avg_mfe_1h={float(stats['avg_mfe_1h_pct']):.2f}% "
            f"avg_mae_1h={float(stats['avg_mae_1h_pct']):.2f}%"
        )
```

- [ ] **Step 5: Run CLI and backtest tests**

Run:

```bash
pytest tests/test_cli.py::test_evaluate_signals_v2_command_prints_group_summary tests/test_trade_backtest.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/altcoin_trend/cli.py src/altcoin_trend/trade_backtest.py tests/test_cli.py
git commit -m "feat: add signal v2 backtest command"
```

## Task 9: Update Operational Strategy Documentation

**Files:**
- Modify: `docs/strategy/current-strategy.md`

- [ ] **Step 1: Update strategy doc with v2 model**

Revise `docs/strategy/current-strategy.md` so it includes:

```markdown
## Signal v2 Model

The system now separates trend strength, signal grade, risk, and actionability:

- `final_score`: trend radar strength.
- `continuation_grade`: `A`, `B`, or empty.
- `ignition_grade`: `EXTREME`, `A`, `B`, or empty.
- `signal_priority`: alert urgency from 0 to 3.
- `chase_risk_score`: 0-100 risk of late entry.
- `actionability_score`: opportunity ranking score.

`trade_candidate` remains compatibility-only and still means continuation is present. It does not include ignition.
```

Also add a table:

```markdown
| Grade | Meaning | Use |
|---|---|---|
| `continuation_A` | Strong confirmed continuation | Main watch signal |
| `continuation_B` | Confirmed continuation with weaker confirmation | Secondary watch |
| `ignition_A` | Higher-quality early breakout | Active early alert |
| `ignition_B` | Early breakout warning | Observe, lower priority |
| `ignition_EXTREME` | RAVE-style explosive move | Immediate attention with chase-risk warning |
```

- [ ] **Step 2: Run documentation sanity check**

Run:

```bash
grep -n "trade_candidate.*continuation" docs/strategy/current-strategy.md
grep -n "ignition_EXTREME" docs/strategy/current-strategy.md
```

Expected: Both commands print matching lines.

- [ ] **Step 3: Commit**

```bash
git add docs/strategy/current-strategy.md
git commit -m "docs: document signal v2 strategy"
```

## Task 10: Full Verification

**Files:**
- No new source files.

- [ ] **Step 1: Run full test suite**

Run:

```bash
pytest -v
```

Expected: PASS.

- [ ] **Step 2: Run focused CLI smoke checks**

Run:

```bash
acts --help
acts opportunities --help
acts evaluate-signals-v2 --help
```

Expected: each command exits 0 and shows help text.

- [ ] **Step 3: Inspect git status**

Run:

```bash
git status --short
```

Expected: only pre-existing unrelated user changes remain, or clean working tree if all earlier work was committed in this branch.

- [ ] **Step 4: Commit verification notes if documentation changed**

If Task 10 only runs verification and changes no files, do not create an empty commit.

## Self-Review Checklist

- Spec coverage:
  - Data model: Task 1.
  - Grading/risk/actionability helpers: Task 2.
  - Scheduler pipeline and compatibility fields: Task 3.
  - Opportunity rank view: Task 4.
  - Alert priority and v2 messages: Task 5.
  - Symbol-level cross-exchange alert dedupe: Task 6.
  - Backtest labels and grouped reports: Tasks 7 and 8.
  - Strategy docs: Task 9.
- Placeholder scan:
  - The plan contains no banned marker words or vague fill-in instructions.
- Type consistency:
  - `continuation_grade` uses `None`, `"A"`, `"B"`.
  - `ignition_grade` uses `None`, `"B"`, `"A"`, `"EXTREME"`.
  - `risk_flags` is a tuple in helper results and a JSON-compatible list in snapshot rows.
  - `actionability_score`, `chase_risk_score`, and `volume_impulse_score` are floats.
