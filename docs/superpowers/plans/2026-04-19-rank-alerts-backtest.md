# Rank Alerts Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make rankings readable and aggregatable, make alerts stricter, document full-market backfill behavior, and add a first signal backtest command.

**Architecture:** Keep ranking snapshots exchange-specific and add symbol aggregation only in the read path. Reuse one high-value signal helper for alert filtering and backtest filtering. Add a small `altcoin_trend.backtest` module that reads existing snapshots and market rows without schema changes.

**Tech Stack:** Python 3.13, Typer CLI, SQLAlchemy Core, pandas where already used, pytest.

---

## File Structure

- Modify: `src/altcoin_trend/signals/ranking.py`
  - Add `aggregate_rank_rows_by_symbol`.
- Modify: `src/altcoin_trend/cli.py`
  - Display `exchange:symbol` in `rank`.
  - Add `--aggregate-symbols`.
  - Report allowlist/full-market mode in bootstrap commands.
  - Add `backtest` command.
- Modify: `src/altcoin_trend/signals/alerts.py`
  - Add `is_high_value_signal`.
  - Gate positive alert generation through it.
  - Include derivatives context in messages when fields exist.
- Modify: `src/altcoin_trend/scheduler.py`
  - Load component scores and derivatives fields for alert rows.
- Create: `src/altcoin_trend/backtest.py`
  - Parse horizons.
  - Load signal candidates.
  - Compute forward returns.
  - Summarize backtest results.
- Modify: `README.md`
  - Document full-market mode and backtest command.
- Modify: `config/acts.env.example`
  - Clarify allowlist and blocklist behavior.
- Add/modify tests:
  - `tests/test_scoring.py` or `tests/test_ranking.py`
  - `tests/test_cli.py`
  - `tests/test_alerts.py`
  - `tests/test_scheduler.py`
  - `tests/test_backtest.py`

---

### Task 1: Rank Display and Symbol Aggregation

**Files:**
- Modify: `src/altcoin_trend/signals/ranking.py`
- Modify: `src/altcoin_trend/cli.py`
- Test: `tests/test_scoring.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing aggregation tests**

Add to `tests/test_scoring.py`:

```python
from altcoin_trend.signals.ranking import aggregate_rank_rows_by_symbol


def test_aggregate_rank_rows_by_symbol_keeps_best_exchange_and_average_score():
    rows = [
        {"rank": 1, "exchange": "binance", "symbol": "ARBUSDT", "final_score": 70.0, "tier": "monitor"},
        {"rank": 2, "exchange": "bybit", "symbol": "ARBUSDT", "final_score": 68.0, "tier": "monitor"},
        {"rank": 3, "exchange": "bybit", "symbol": "OPUSDT", "final_score": 60.0, "tier": "monitor"},
    ]

    aggregated = aggregate_rank_rows_by_symbol(rows)

    assert len(aggregated) == 2
    assert aggregated[0]["symbol"] == "ARBUSDT"
    assert aggregated[0]["exchange"] == "binance"
    assert aggregated[0]["final_score"] == 70.0
    assert aggregated[0]["exchange_count"] == 2
    assert aggregated[0]["average_score"] == 69.0
    assert aggregated[0]["rank"] == 1
    assert aggregated[1]["symbol"] == "OPUSDT"
    assert aggregated[1]["rank"] == 2
```

- [ ] **Step 2: Run aggregation test and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_scoring.py::test_aggregate_rank_rows_by_symbol_keeps_best_exchange_and_average_score -q
```

Expected: fail because `aggregate_rank_rows_by_symbol` does not exist.

- [ ] **Step 3: Implement aggregation**

Add to `src/altcoin_trend/signals/ranking.py`:

```python
from collections import defaultdict
from typing import Any, Mapping


def aggregate_rank_rows_by_symbol(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        if not symbol:
            continue
        grouped[symbol].append(dict(row))

    aggregated: list[dict[str, Any]] = []
    for symbol, symbol_rows in grouped.items():
        symbol_rows.sort(key=lambda item: float(item.get("final_score", 0.0)), reverse=True)
        best = dict(symbol_rows[0])
        scores = [float(item.get("final_score", 0.0)) for item in symbol_rows]
        best["symbol"] = symbol
        best["exchange"] = str(best.get("exchange") or "unknown")
        best["exchange_count"] = len(symbol_rows)
        best["average_score"] = round(sum(scores) / len(scores), 4)
        aggregated.append(best)

    aggregated.sort(key=lambda item: float(item.get("final_score", 0.0)), reverse=True)
    for index, row in enumerate(aggregated, start=1):
        row["rank"] = index
    return aggregated
```

- [ ] **Step 4: Verify aggregation GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_scoring.py::test_aggregate_rank_rows_by_symbol_keeps_best_exchange_and_average_score -q
```

Expected: pass.

- [ ] **Step 5: Write failing CLI rank output tests**

Update `tests/test_cli.py`:

```python
def test_cli_rank_prints_exchange_qualified_symbol(monkeypatch):
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.load_rank_rows",
        lambda engine, rank_scope, limit: [
            {"rank": 1, "exchange": "binance", "symbol": "SOLUSDT", "final_score": 88.4, "tier": "strong"}
        ],
    )

    result = CliRunner().invoke(app, ["rank", "--limit", "5"])

    assert result.exit_code == 0
    assert "1. binance:SOLUSDT score=88.4 tier=strong" in result.output


def test_cli_rank_can_aggregate_symbols(monkeypatch):
    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.load_rank_rows",
        lambda engine, rank_scope, limit: [
            {"rank": 1, "exchange": "binance", "symbol": "ARBUSDT", "final_score": 70.0, "tier": "monitor"},
            {"rank": 2, "exchange": "bybit", "symbol": "ARBUSDT", "final_score": 68.0, "tier": "monitor"},
        ],
    )

    result = CliRunner().invoke(app, ["rank", "--limit", "5", "--aggregate-symbols"])

    assert result.exit_code == 0
    assert "aggregate_symbols=True" in result.output
    assert "1. binance:ARBUSDT score=70.0 tier=monitor exchanges=2 avg_score=69.0" in result.output
    assert "2. bybit:ARBUSDT" not in result.output
```

- [ ] **Step 6: Run CLI tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py::test_cli_rank_prints_exchange_qualified_symbol tests/test_cli.py::test_cli_rank_can_aggregate_symbols -q
```

Expected: first test fails because output lacks exchange, second fails because option is missing.

- [ ] **Step 7: Implement CLI rank output**

In `src/altcoin_trend/cli.py`:

```python
from altcoin_trend.signals.ranking import aggregate_rank_rows_by_symbol
```

Update `rank` signature and body:

```python
def rank(
    limit: int = typer.Option(30, "--limit", min=1),
    exchange: str | None = typer.Option(None, "--exchange"),
    aggregate_symbols: bool = typer.Option(False, "--aggregate-symbols"),
) -> None:
    scope = exchange or "all"
    settings = load_settings()
    engine = build_engine(settings)
    rows = load_rank_rows(engine, rank_scope=scope, limit=limit)
    if aggregate_symbols:
        rows = aggregate_rank_rows_by_symbol(rows)
    if not rows:
        typer.echo(f"No rank snapshot found for scope={scope}")
        return
    typer.echo(f"Rank snapshot scope={scope} limit={limit} aggregate_symbols={aggregate_symbols}")
    for row in rows:
        exchange_name = row.get("exchange") or "unknown"
        line = f"{row['rank']}. {exchange_name}:{row['symbol']} score={row['final_score']} tier={row['tier']}"
        if aggregate_symbols:
            line += f" exchanges={row['exchange_count']} avg_score={row['average_score']}"
        typer.echo(line)
```

- [ ] **Step 8: Verify Task 1**

Run:

```bash
.venv/bin/python -m pytest tests/test_scoring.py tests/test_cli.py -q
```

Expected: pass.

- [ ] **Step 9: Commit Task 1**

```bash
git add src/altcoin_trend/signals/ranking.py src/altcoin_trend/cli.py tests/test_scoring.py tests/test_cli.py
git commit -m "feat: aggregate rank output by symbol"
```

---

### Task 2: Full-Market Backfill Mode Output and Docs

**Files:**
- Modify: `src/altcoin_trend/cli.py`
- Modify: `README.md`
- Modify: `config/acts.env.example`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests for selection mode**

Add to `tests/test_cli.py`:

```python
def test_cli_bootstrap_reports_full_market_mode(monkeypatch):
    monkeypatch.setattr(
        "altcoin_trend.cli.load_settings",
        lambda: AppSettings(default_exchanges="binance", symbol_allowlist="", symbol_blocklist="DOGEUSDT"),
    )
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())

    class Result:
        exchange = "binance"
        instruments_selected = 12
        bars_written = 34

    monkeypatch.setattr("altcoin_trend.cli.bootstrap_exchange", lambda **kwargs: Result())

    result = CliRunner().invoke(app, ["bootstrap", "--lookback-days", "1"])

    assert result.exit_code == 0
    assert "mode=full-market blocklist=1" in result.output


def test_cli_bootstrap_derivatives_reports_allowlist_mode(monkeypatch):
    monkeypatch.setattr(
        "altcoin_trend.cli.load_settings",
        lambda: AppSettings(default_exchanges="binance", symbol_allowlist="BTCUSDT,ETHUSDT"),
    )
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr("altcoin_trend.cli.bootstrap_derivatives", lambda **kwargs: 7)

    result = CliRunner().invoke(app, ["bootstrap-derivatives", "--lookback-days", "1"])

    assert result.exit_code == 0
    assert "mode=allowlist allowlist=2 blocklist=0" in result.output
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py::test_cli_bootstrap_reports_full_market_mode tests/test_cli.py::test_cli_bootstrap_derivatives_reports_allowlist_mode -q
```

Expected: fail because mode text is not printed.

- [ ] **Step 3: Implement selection mode helper**

Add to `src/altcoin_trend/cli.py`:

```python
def _selection_mode_text(settings) -> str:
    allowlist_count = len(settings.allowlist_symbols)
    blocklist_count = len(settings.blocklist_symbols)
    mode = "allowlist" if allowlist_count else "full-market"
    return f"mode={mode} allowlist={allowlist_count} blocklist={blocklist_count}"
```

Print it once near the start of `bootstrap` and `bootstrap_derivatives_command`:

```python
typer.echo(f"Selection {_selection_mode_text(settings)}")
```

- [ ] **Step 4: Verify CLI tests GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py::test_cli_bootstrap_reports_full_market_mode tests/test_cli.py::test_cli_bootstrap_derivatives_reports_allowlist_mode -q
```

Expected: pass.

- [ ] **Step 5: Update docs**

In `README.md`, add:

```markdown
## Market Selection

Leave `ACTS_SYMBOL_ALLOWLIST` empty to run full-market mode across all eligible
USDT perpetual contracts. The system still applies quote asset, market type,
trading status, minimum listing age, and blocklist filters. Set
`ACTS_SYMBOL_ALLOWLIST=BTCUSDT,ETHUSDT,SOLUSDT` for a small development universe.
```

In `config/acts.env.example`, replace the allowlist lines with:

```text
# Empty allowlist means full-market mode after liquidity/listing/blocklist filters.
# Set comma-separated symbols for a smaller development universe.
ACTS_SYMBOL_ALLOWLIST=
# Comma-separated symbols to exclude in both full-market and allowlist modes.
ACTS_SYMBOL_BLOCKLIST=
```

- [ ] **Step 6: Verify Task 2**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -q
```

Expected: pass.

- [ ] **Step 7: Commit Task 2**

```bash
git add src/altcoin_trend/cli.py tests/test_cli.py README.md config/acts.env.example
git commit -m "feat: report market selection mode"
```

---

### Task 3: High-Value Alert Gate

**Files:**
- Modify: `src/altcoin_trend/signals/alerts.py`
- Modify: `src/altcoin_trend/scheduler.py`
- Test: `tests/test_alerts.py`
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing high-value gate tests**

Add to `tests/test_alerts.py`:

```python
from altcoin_trend.signals.alerts import is_high_value_signal


def _high_value_row(**overrides):
    row = {
        "tier": "strong",
        "trend_score": 80.0,
        "relative_strength_score": 75.0,
        "derivatives_score": 60.0,
        "quality_score": 90.0,
        "volume_breakout_score": 45.0,
        "veto_reason_codes": [],
    }
    row.update(overrides)
    return row


def test_is_high_value_signal_requires_all_quality_components():
    assert is_high_value_signal(_high_value_row()) is True
    assert is_high_value_signal(_high_value_row(tier="rejected")) is False
    assert is_high_value_signal(_high_value_row(trend_score=74.9)) is False
    assert is_high_value_signal(_high_value_row(relative_strength_score=69.9)) is False
    assert is_high_value_signal(_high_value_row(derivatives_score=54.9)) is False
    assert is_high_value_signal(_high_value_row(quality_score=79.9)) is False
    assert is_high_value_signal(_high_value_row(volume_breakout_score=39.9)) is False
    assert is_high_value_signal(_high_value_row(veto_reason_codes=["funding_heat"])) is False
```

- [ ] **Step 2: Verify gate test RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_alerts.py::test_is_high_value_signal_requires_all_quality_components -q
```

Expected: fail because helper does not exist.

- [ ] **Step 3: Implement high-value helper**

Add to `src/altcoin_trend/signals/alerts.py`:

```python
def _float_value(row: Mapping[str, Any] | Any, key: str, default: float = 0.0) -> float:
    value = _get(row, key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def is_high_value_signal(row: Mapping[str, Any] | Any) -> bool:
    tier = str(_get(row, "tier", "rejected"))
    veto_codes = _normalize_items(_get(row, "veto_reason_codes", ()))
    return (
        tier in {"watchlist", "strong"}
        and _float_value(row, "trend_score") >= 75.0
        and _float_value(row, "relative_strength_score") >= 70.0
        and _float_value(row, "derivatives_score") >= 55.0
        and _float_value(row, "quality_score") >= 80.0
        and _float_value(row, "volume_breakout_score") >= 40.0
        and not veto_codes
    )
```

- [ ] **Step 4: Verify gate GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_alerts.py::test_is_high_value_signal_requires_all_quality_components -q
```

Expected: pass.

- [ ] **Step 5: Write failing alert gating tests**

Add to `tests/test_alerts.py`:

```python
def test_build_alert_event_rows_suppresses_positive_alert_when_signal_is_not_high_value():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    weak_row = {
        "asset_id": 17,
        "exchange": "binance",
        "symbol": "SOLUSDT",
        "tier": "strong",
        "final_score": 88.4,
        "trend_score": 90.0,
        "volume_breakout_score": 20.0,
        "relative_strength_score": 80.0,
        "derivatives_score": 70.0,
        "quality_score": 100.0,
        "veto_reason_codes": [],
    }

    assert build_alert_event_rows([weak_row], recent_events=[], now=now, cooldown_seconds=3600) == []
```

- [ ] **Step 6: Run alert gating test and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_alerts.py::test_build_alert_event_rows_suppresses_positive_alert_when_signal_is_not_high_value -q
```

Expected: fail because current logic alerts on `strong`.

- [ ] **Step 7: Gate positive alert generation**

In `build_alert_event_rows`, compute:

```python
high_value = is_high_value_signal(row)
positive_breakout = current_tier == "strong" and previous_tier not in {"strong", "watchlist"} and high_value
```

Call `evaluate_transition` with:

```python
breakout_confirmed=positive_breakout or high_value,
oi_confirmed=high_value,
```

After `decision`, suppress positive decisions that are not high-value:

```python
if decision.alert_type in {"strong_trend", "watchlist_enter", "breakout_confirmed"} and not high_value:
    continue
```

- [ ] **Step 8: Improve alert message derivatives context**

Add optional lines in `build_strong_alert_message`:

```python
optional_fields = (
    ("OI delta 1h", "oi_delta_1h"),
    ("OI delta 4h", "oi_delta_4h"),
    ("Funding z-score", "funding_zscore"),
    ("Taker buy/sell ratio", "taker_buy_sell_ratio"),
)
for label, key in optional_fields:
    value = _get(row, key, None)
    if value is not None:
        lines.append(f"{label}: {value}")
```

- [ ] **Step 9: Update scheduler rank loader fields**

In `src/altcoin_trend/scheduler.py`, update `load_rank_rows` SQL to join `alt_signal.feature_snapshot AS fs` on `asset_id` and `ts`, and select:

```sql
fs.trend_score,
fs.volume_breakout_score,
fs.relative_strength_score,
fs.derivatives_score,
fs.quality_score,
fs.oi_delta_1h,
fs.oi_delta_4h,
fs.funding_zscore,
fs.taker_buy_sell_ratio,
fs.veto_reason_codes
```

Keep existing rank fields.

- [ ] **Step 10: Add scheduler SQL regression test**

Add to `tests/test_scheduler.py`:

```python
def test_load_rank_rows_selects_alert_component_fields():
    captured = {}

    class Result:
        def mappings(self):
            return self

        def all(self):
            return []

    class Connection:
        def execute(self, statement, params):
            captured["sql"] = str(statement)
            return Result()

    class Begin:
        def __enter__(self):
            return Connection()

        def __exit__(self, exc_type, exc, tb):
            return False

    class Engine:
        def begin(self):
            return Begin()

    from altcoin_trend.scheduler import load_rank_rows

    load_rank_rows(Engine(), rank_scope="all", limit=10)

    assert "alt_signal.feature_snapshot AS fs" in captured["sql"]
    assert "fs.trend_score" in captured["sql"]
    assert "fs.derivatives_score" in captured["sql"]
    assert "fs.veto_reason_codes" in captured["sql"]
```

- [ ] **Step 11: Verify Task 3**

Run:

```bash
.venv/bin/python -m pytest tests/test_alerts.py tests/test_scheduler.py -q
```

Expected: pass.

- [ ] **Step 12: Commit Task 3**

```bash
git add src/altcoin_trend/signals/alerts.py src/altcoin_trend/scheduler.py tests/test_alerts.py tests/test_scheduler.py
git commit -m "feat: gate alerts on high value signals"
```

---

### Task 4: First Backtest Command

**Files:**
- Create: `src/altcoin_trend/backtest.py`
- Modify: `src/altcoin_trend/cli.py`
- Test: `tests/test_backtest.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing horizon parser tests**

Create `tests/test_backtest.py`:

```python
from datetime import timedelta

import pytest

from altcoin_trend.backtest import parse_horizons


def test_parse_horizons_accepts_hour_and_day_values():
    assert parse_horizons("1h,4h,24h,1d") == (
        ("1h", timedelta(hours=1)),
        ("4h", timedelta(hours=4)),
        ("24h", timedelta(hours=24)),
        ("1d", timedelta(days=1)),
    )


def test_parse_horizons_rejects_invalid_values():
    with pytest.raises(ValueError, match="Invalid horizon"):
        parse_horizons("90m")
```

- [ ] **Step 2: Run parser tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_backtest.py -q
```

Expected: fail because module does not exist.

- [ ] **Step 3: Implement parser and dataclasses**

Create `src/altcoin_trend/backtest.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine, text

from altcoin_trend.signals.alerts import is_high_value_signal


@dataclass(frozen=True)
class HorizonStats:
    observations: int
    average_return_pct: float
    win_rate: float


@dataclass(frozen=True)
class BacktestSummary:
    signal_count: int
    average_score: float
    tier_counts: dict[str, int]
    exchange_counts: dict[str, int]
    horizon_stats: dict[str, HorizonStats]
    top_signals: list[dict[str, Any]]


def parse_horizons(value: str) -> tuple[tuple[str, timedelta], ...]:
    horizons: list[tuple[str, timedelta]] = []
    for raw_item in value.split(","):
        item = raw_item.strip().lower()
        if not item:
            continue
        unit = item[-1]
        number_text = item[:-1]
        if not number_text.isdigit() or unit not in {"h", "d"}:
            raise ValueError(f"Invalid horizon: {raw_item}")
        number = int(number_text)
        if number < 1:
            raise ValueError(f"Invalid horizon: {raw_item}")
        delta = timedelta(hours=number) if unit == "h" else timedelta(days=number)
        horizons.append((item, delta))
    if not horizons:
        raise ValueError("At least one horizon is required")
    return tuple(horizons)
```

- [ ] **Step 4: Verify parser GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_backtest.py -q
```

Expected: pass.

- [ ] **Step 5: Write failing pure summary test**

Add to `tests/test_backtest.py`:

```python
from altcoin_trend.backtest import summarize_backtest


def test_summarize_backtest_computes_counts_average_returns_and_win_rate():
    signals = [
        {"exchange": "binance", "symbol": "SOLUSDT", "tier": "strong", "final_score": 90.0},
        {"exchange": "bybit", "symbol": "ARBUSDT", "tier": "watchlist", "final_score": 80.0},
    ]
    returns = {
        "1h": [2.0, -1.0],
        "4h": [5.0],
    }

    summary = summarize_backtest(signals, returns, limit=1)

    assert summary.signal_count == 2
    assert summary.average_score == 85.0
    assert summary.tier_counts == {"strong": 1, "watchlist": 1}
    assert summary.exchange_counts == {"binance": 1, "bybit": 1}
    assert summary.horizon_stats["1h"].observations == 2
    assert summary.horizon_stats["1h"].average_return_pct == 0.5
    assert summary.horizon_stats["1h"].win_rate == 0.5
    assert summary.top_signals == [signals[0]]
```

- [ ] **Step 6: Run summary test and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_backtest.py::test_summarize_backtest_computes_counts_average_returns_and_win_rate -q
```

Expected: fail because `summarize_backtest` does not exist.

- [ ] **Step 7: Implement summary helper**

Add to `src/altcoin_trend/backtest.py`:

```python
def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def summarize_backtest(
    signals: list[dict[str, Any]],
    returns_by_horizon: dict[str, list[float]],
    limit: int,
) -> BacktestSummary:
    scores = [float(row.get("final_score", 0.0)) for row in signals]
    horizon_stats: dict[str, HorizonStats] = {}
    for label, returns in returns_by_horizon.items():
        if not returns:
            horizon_stats[label] = HorizonStats(observations=0, average_return_pct=0.0, win_rate=0.0)
            continue
        horizon_stats[label] = HorizonStats(
            observations=len(returns),
            average_return_pct=round(sum(returns) / len(returns), 4),
            win_rate=round(sum(1 for value in returns if value > 0) / len(returns), 4),
        )
    top_signals = sorted(signals, key=lambda row: float(row.get("final_score", 0.0)), reverse=True)[:limit]
    return BacktestSummary(
        signal_count=len(signals),
        average_score=round(sum(scores) / len(scores), 4) if scores else 0.0,
        tier_counts=_count_by(signals, "tier"),
        exchange_counts=_count_by(signals, "exchange"),
        horizon_stats=horizon_stats,
        top_signals=top_signals,
    )
```

- [ ] **Step 8: Implement DB loader and runner**

Add to `src/altcoin_trend/backtest.py`:

```python
def _load_signal_rows(engine: Engine, start: datetime, end: datetime, min_score: float) -> list[dict[str, Any]]:
    statement = text(
        """
        SELECT
            fs.ts,
            fs.asset_id,
            fs.exchange,
            fs.symbol,
            fs.close,
            fs.final_score,
            COALESCE(r.tier, 'rejected') AS tier,
            fs.trend_score,
            fs.volume_breakout_score,
            fs.relative_strength_score,
            fs.derivatives_score,
            fs.quality_score,
            fs.oi_delta_1h,
            fs.oi_delta_4h,
            fs.funding_zscore,
            fs.taker_buy_sell_ratio,
            fs.veto_reason_codes
        FROM alt_signal.feature_snapshot AS fs
        LEFT JOIN alt_signal.rank_snapshot AS r
          ON r.asset_id = fs.asset_id
         AND r.ts = fs.ts
         AND r.rank_scope = fs.exchange
        WHERE fs.ts >= :start
          AND fs.ts < :end
          AND fs.final_score >= :min_score
        ORDER BY fs.final_score DESC
        """
    )
    with engine.begin() as connection:
        return [dict(row) for row in connection.execute(statement, {"start": start, "end": end, "min_score": min_score}).mappings()]


def _future_close(engine: Engine, asset_id: int, target_ts: datetime) -> float | None:
    statement = text(
        """
        SELECT close
        FROM alt_core.market_1m
        WHERE asset_id = :asset_id
          AND ts >= :target_ts
        ORDER BY ts
        LIMIT 1
        """
    )
    with engine.begin() as connection:
        row = connection.execute(statement, {"asset_id": asset_id, "target_ts": target_ts}).mappings().first()
    return float(row["close"]) if row is not None else None


def run_signal_backtest(
    engine: Engine,
    start: datetime,
    end: datetime,
    min_score: float,
    horizons: tuple[tuple[str, timedelta], ...],
    high_value_only: bool,
    limit: int,
) -> BacktestSummary:
    if start >= end:
        raise ValueError("--from must be before --to")
    signals = _load_signal_rows(engine, start, end, min_score)
    if high_value_only:
        signals = [row for row in signals if is_high_value_signal(row)]
    returns_by_horizon = {label: [] for label, _ in horizons}
    for signal in signals:
        entry_close = float(signal["close"])
        if entry_close <= 0:
            continue
        for label, delta in horizons:
            future = _future_close(engine, int(signal["asset_id"]), signal["ts"] + delta)
            if future is None:
                continue
            returns_by_horizon[label].append(round(((future / entry_close) - 1.0) * 100.0, 4))
    return summarize_backtest(signals, returns_by_horizon, limit=limit)
```

- [ ] **Step 9: Write failing CLI backtest test**

Add to `tests/test_cli.py`:

```python
def test_cli_backtest_prints_summary(monkeypatch):
    from altcoin_trend.backtest import BacktestSummary, HorizonStats

    monkeypatch.setattr("altcoin_trend.cli.load_settings", lambda: AppSettings())
    monkeypatch.setattr("altcoin_trend.cli.build_engine", lambda settings: object())
    monkeypatch.setattr(
        "altcoin_trend.cli.run_signal_backtest",
        lambda **kwargs: BacktestSummary(
            signal_count=2,
            average_score=85.0,
            tier_counts={"strong": 1, "watchlist": 1},
            exchange_counts={"binance": 2},
            horizon_stats={"1h": HorizonStats(observations=2, average_return_pct=1.5, win_rate=0.5)},
            top_signals=[{"exchange": "binance", "symbol": "SOLUSDT", "final_score": 90.0, "tier": "strong"}],
        ),
    )

    result = CliRunner().invoke(app, ["backtest", "--from", "2026-03-19", "--to", "2026-04-19"])

    assert result.exit_code == 0
    assert "Backtest signals=2 average_score=85.0" in result.output
    assert "1h observations=2 avg_return=1.5 win_rate=0.5" in result.output
    assert "binance:SOLUSDT score=90.0 tier=strong" in result.output
```

- [ ] **Step 10: Run CLI backtest test and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py::test_cli_backtest_prints_summary -q
```

Expected: fail because command is missing.

- [ ] **Step 11: Implement CLI backtest command**

In `src/altcoin_trend/cli.py`, import:

```python
from altcoin_trend.backtest import parse_horizons, run_signal_backtest
```

Add:

```python
def _parse_cli_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@app.command("backtest")
def backtest(
    start_date: str = typer.Option(..., "--from"),
    end_date: str = typer.Option(..., "--to"),
    min_score: float = typer.Option(60.0, "--min-score"),
    horizons: str = typer.Option("1h,4h,24h", "--horizons"),
    high_value_only: bool = typer.Option(False, "--high-value-only"),
    limit: int = typer.Option(10, "--limit", min=1),
) -> None:
    try:
        parsed_horizons = parse_horizons(horizons)
        start = _parse_cli_date(start_date)
        end = _parse_cli_date(end_date)
        if start >= end:
            raise ValueError("--from must be before --to")
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    settings = load_settings()
    engine = build_engine(settings)
    summary = run_signal_backtest(
        engine=engine,
        start=start,
        end=end,
        min_score=min_score,
        horizons=parsed_horizons,
        high_value_only=high_value_only,
        limit=limit,
    )
    if summary.signal_count == 0:
        typer.echo("No signals found for requested filters")
        return
    typer.echo(f"Backtest signals={summary.signal_count} average_score={summary.average_score}")
    typer.echo(f"Tiers: {summary.tier_counts}")
    typer.echo(f"Exchanges: {summary.exchange_counts}")
    for label, stats in summary.horizon_stats.items():
        typer.echo(
            f"{label} observations={stats.observations} "
            f"avg_return={stats.average_return_pct} win_rate={stats.win_rate}"
        )
    typer.echo("Top signals:")
    for row in summary.top_signals:
        typer.echo(f"{row['exchange']}:{row['symbol']} score={row['final_score']} tier={row['tier']}")
```

- [ ] **Step 12: Verify Task 4**

Run:

```bash
.venv/bin/python -m pytest tests/test_backtest.py tests/test_cli.py -q
```

Expected: pass.

- [ ] **Step 13: Commit Task 4**

```bash
git add src/altcoin_trend/backtest.py src/altcoin_trend/cli.py tests/test_backtest.py tests/test_cli.py
git commit -m "feat: add signal backtest command"
```

---

### Task 5: Final Integration and Operational Smoke Tests

**Files:**
- Modify as needed only if verification reveals integration bugs.

- [ ] **Step 1: Run full test suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run CLI help**

Run:

```bash
.venv/bin/acts --help
```

Expected: includes `backtest`.

- [ ] **Step 3: Run rank smoke checks**

Run:

```bash
.venv/bin/acts rank --limit 5
.venv/bin/acts rank --limit 5 --aggregate-symbols
```

Expected: first output displays exchange-qualified rows, second output returns unique symbols.

- [ ] **Step 4: Run backtest smoke check against local DB**

Run:

```bash
.venv/bin/acts backtest --from 2026-03-19 --to 2026-04-19 --min-score 50 --horizons 1h,4h,24h --limit 5
```

Expected: prints a summary or `No signals found for requested filters`.

- [ ] **Step 5: Run git status**

Run:

```bash
git status --short
```

Expected: clean.

- [ ] **Step 6: Commit final docs if changed**

If Task 5 required docs or minor integration fixes:

```bash
git add <changed-files>
git commit -m "docs: document backtest and full market workflow"
```

If no files changed, skip this commit.
