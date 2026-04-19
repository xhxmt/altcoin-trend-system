# Rank, Alerts, Full-Market Backfill, and Backtest Design

## Goal

Improve the altcoin trend system so the current rankings are easier to read, alerts are stricter and more useful, full-market data collection is easier to operate, and the system has a first backtest command for evaluating generated signals.

This version does not add trading execution, position sizing, portfolio accounting, fees, or slippage. It prepares those later steps by producing structured signal candidates and forward-return statistics.

## Current State

The system can:

- Backfill Binance and Bybit USDT perpetual 1m market data.
- Backfill public derivatives observations into `alt_core.market_1m`.
- Generate `alt_signal.feature_snapshot` and `alt_signal.rank_snapshot`.
- Explain a single exchange-symbol signal.
- Process Telegram alerts from the latest ranking snapshot.

Known issues:

- `acts rank` prints only `symbol`, so Binance and Bybit rows for the same symbol look duplicated.
- The `all` ranking scope ranks exchange-specific assets directly and has no symbol-level aggregation view.
- Positive alerts are mostly tied to broad tier transitions. They do not explicitly require the combination of trend quality, relative strength, derivatives confirmation, and data quality.
- Full-market mode is available by leaving `ACTS_SYMBOL_ALLOWLIST` empty, but this is not obvious from CLI output or docs.
- There is no backtest command to inspect whether generated signals had favorable forward returns.

## Design Summary

### 1. Rank Readability and Symbol Aggregation

`acts rank` will always display `exchange:symbol` for exchange-level rows.

The command will also gain:

```bash
acts rank --limit 20 --aggregate-symbols
```

When `--aggregate-symbols` is enabled:

- Rows with the same `symbol` across exchanges are collapsed into one output row.
- The representative score is the best exchange score.
- The output includes the best exchange, exchange count, and average score.
- Sorting uses representative score descending.

Example output:

```text
Rank snapshot scope=all limit=5 aggregate_symbols=True
1. binance:ARBUSDT score=70.2364 tier=monitor exchanges=2 avg_score=70.0625
2. binance:OPUSDT score=57.2615 tier=rejected exchanges=2 avg_score=57.2058
```

This preserves the existing exchange-specific rank snapshots and adds aggregation only in the read path. No schema migration is required.

### 2. High-Value Alert Filtering

Positive alerts will require a dedicated high-value signal gate. A row qualifies only when all of these are true:

- `tier` is `watchlist` or `strong`.
- `trend_score >= 75`.
- `relative_strength_score >= 70`.
- `derivatives_score >= 55`.
- `quality_score >= 80`.
- `volume_breakout_score >= 40`.
- No `veto_reason_codes`.

The alert builder will expose this gate as a focused helper so the thresholds are testable and easy to tune.

Alert transitions will use:

- `strong_trend` when the row is `strong` and passes the high-value gate.
- `watchlist_enter` when the row enters `watchlist` and passes the high-value gate.
- `risk_downgrade` when a previously strong/watchlist row falls in tier or gains veto reasons.

The current `breakout_confirmed` branch is noisy for this system stage and will not create positive alerts unless the row also passes the high-value gate. This keeps Telegram focused on fewer, higher-quality events.

Telegram message content will include the same core signal components already in the row:

- Exchange and symbol.
- Final score and tier.
- Trend, volume, relative strength, derivatives, and quality scores.
- OI 1h/4h deltas when available.
- Funding z-score when available.
- Taker buy/sell ratio when available.
- Reasons and risks.

### 3. Full-Market Backfill Operation

Full-market mode will remain configuration-driven:

- Empty `ACTS_SYMBOL_ALLOWLIST` means all eligible USDT perpetual contracts.
- `ACTS_SYMBOL_BLOCKLIST` excludes symbols.
- Existing liquidity, quote asset, market type, status, and listing-age filters still apply.

The CLI will make this visible:

- `acts bootstrap` will report whether it is running in allowlist or full-market mode.
- `acts bootstrap-derivatives` will do the same.
- `README.md` and `config/acts.env.example` will document the operational meaning.

No new exchange selection model is needed.

### 4. Backtest First Version

Add:

```bash
acts backtest --from 2026-03-19 --to 2026-04-19 --min-score 60 --horizons 1h,4h,24h
```

The first version reads historical `alt_signal.feature_snapshot` rows and joins future `alt_core.market_1m` closes for the requested horizons.

The backtest will:

- Select feature snapshots between `--from` and `--to`.
- Filter by `--min-score`.
- Optionally filter to high-value signals with `--high-value-only`.
- Compute forward returns from snapshot close to the nearest market close at or after each horizon timestamp.
- Print a compact summary:
  - signal count
  - average final score
  - count by tier
  - count by exchange
  - average return per horizon
  - win rate per horizon where return is greater than zero
- Print the top signal rows by score for manual inspection.

This command uses existing tables and does not write results to the database in the first version.

## Components

### `altcoin_trend.signals.ranking`

Responsibilities:

- Keep `rank_scores` unchanged for snapshot generation.
- Add a pure function for aggregating already-loaded rank rows by symbol.

Proposed API:

```python
def aggregate_rank_rows_by_symbol(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ...
```

### `altcoin_trend.signals.alerts`

Responsibilities:

- Add high-value gate helper.
- Use the helper in `build_alert_event_rows`.
- Improve message text using optional derivatives fields.

Proposed API:

```python
def is_high_value_signal(row: Mapping[str, Any] | Any) -> bool:
    ...
```

### `altcoin_trend.scheduler`

Responsibilities:

- Load rank rows with fields needed by alert gating and messaging.
- Keep snapshot writing behavior unchanged.

### `altcoin_trend.backtest`

Responsibilities:

- Parse horizons.
- Load historical feature rows.
- Compute forward returns from market rows.
- Summarize the result.

Proposed API:

```python
@dataclass(frozen=True)
class BacktestSummary:
    signal_count: int
    average_score: float
    tier_counts: dict[str, int]
    exchange_counts: dict[str, int]
    horizon_stats: dict[str, HorizonStats]
    top_signals: list[dict[str, Any]]

def run_signal_backtest(
    engine: Engine,
    start: datetime,
    end: datetime,
    min_score: float,
    horizons: tuple[timedelta, ...],
    high_value_only: bool,
    limit: int,
) -> BacktestSummary:
    ...
```

### `altcoin_trend.cli`

Responsibilities:

- Update `rank` output and add `--aggregate-symbols`.
- Update bootstrap command output to show allowlist/full-market mode.
- Add `backtest` command.

## Data Flow

Rank display:

1. `acts rank` calls `load_rank_rows`.
2. Without aggregation, rows print as `exchange:symbol`.
3. With aggregation, rows pass through `aggregate_rank_rows_by_symbol`.
4. Aggregated rows print the representative exchange, score, tier, exchange count, and average score.

Alert processing:

1. `process_alerts` loads latest rank rows with component scores.
2. `build_alert_event_rows` evaluates previous tier and high-value qualification.
3. Qualifying alert rows are inserted into `alt_signal.alert_events`.
4. Telegram sends only inserted rows when configured.

Backtest:

1. CLI parses date range and horizons.
2. Backtest loader reads feature snapshots in range.
3. Optional high-value filtering is applied in Python with the same helper as alerts.
4. For each signal and horizon, the nearest future market close is loaded.
5. Summary statistics are printed.

## Error Handling

- Invalid horizon strings produce a CLI parameter error.
- `--from` must be before `--to`.
- Empty backtest result prints `No signals found for requested filters`.
- Missing future market data excludes that signal-horizon pair from that horizon's return stats, while keeping the signal in the signal count.
- Aggregation handles missing `exchange` values by displaying `unknown`.

## Testing

Tests will cover:

- `acts rank` prints `exchange:symbol`.
- `acts rank --aggregate-symbols` collapses duplicate symbols and displays exchange count and average score.
- High-value gate accepts a complete strong row and rejects rows with weak trend, weak relative strength, weak derivatives, weak quality, weak volume, rejected tier, or veto.
- Alert rows are generated only for high-value positive transitions.
- Alert messages include derivatives context when present.
- Bootstrap CLI output reports allowlist vs full-market mode.
- Horizon parsing accepts `1h,4h,24h` and rejects unsupported formats.
- Backtest summary computes signal count, tier counts, exchange counts, average return, and win rate.
- `acts backtest` prints summary rows from a mocked backtest result.

## Out of Scope

- Trading execution.
- Portfolio simulation.
- Position sizing.
- Fees and slippage.
- Database persistence for backtest runs.
- UI/dashboard work.
- Machine learning model training.

## Acceptance Criteria

- Full test suite passes.
- `acts rank --limit 5` displays exchange-qualified rows.
- `acts rank --limit 5 --aggregate-symbols` returns unique symbols.
- `acts bootstrap --lookback-days 1` output indicates allowlist or full-market mode.
- `acts bootstrap-derivatives --lookback-days 1` output indicates allowlist or full-market mode.
- `acts backtest --from <date> --to <date>` runs against the existing database and prints a summary or a clear empty-result message.
- Existing `run-once`, `explain`, and `alerts` commands continue to work.
