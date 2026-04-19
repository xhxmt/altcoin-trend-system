# Backfill Pagination and Derivatives Confirmation Design

Date: 2026-04-19

## Summary

Improve the Altcoin Trend System so it can produce useful live rankings from a real local dataset and confirm trend signals with low-frequency derivatives data.

This iteration has two connected goals:

1. Fix historical 1-minute kline bootstrap so `--lookback-days 31` actually backfills the requested window instead of fetching a single exchange-limited page.
2. Replace the fixed `derivatives_score = 50.0` with a data-driven score using open interest, funding rate, and long/short account ratio when public exchange data is available.

The system remains a scanner and alerting tool. It still does not trade, does not require private exchange API keys, and does not add a web UI.

## Current State

The database has been created and migrations have been applied:

```text
database: altcoin_trend
schemas: alt_ingest, alt_raw, alt_core, alt_signal
current rows: empty
```

The existing `bootstrap_exchange` calls `fetch_klines_1m(symbol, start_ms, end_ms)` once per symbol. Binance and Bybit both limit the number of klines returned by a single REST call, so a 31-day or 90-day bootstrap does not currently guarantee a complete 1-minute history.

Recent signal work made trend continuation and relative strength data-driven. The remaining fixed score in the main ranking formula is `derivatives_score`, which is currently `50.0` for every asset.

## External API References

The design uses only public endpoints.

Binance USD-M Futures:

- Kline data: existing adapter already uses `/fapi/v1/klines`.
- Funding history: `/fapi/v1/fundingRate`, max `limit` 1000, ascending order, public.
- Open interest statistics: `/futures/data/openInterestHist`, periods include `5m`, `15m`, `1h`, `4h`, `1d`; only latest 1 month is available.

Bybit V5:

- Kline data: existing adapter already uses `/v5/market/kline`.
- Funding history: `/v5/market/funding/history`, max `limit` 200, public.
- Open interest: `/v5/market/open-interest`, supports `5min`, `15min`, `1h`, `4h`, `1d` and cursor pagination.
- Long/short account ratio: `/v5/market/account-ratio`, supports `5min`, `15min`, `1h`, `4h`, `1d` and cursor pagination.

## Goals

1. Make bootstrap fetch complete 1-minute kline ranges across exchange page limits.
2. Keep backfill idempotent so reruns do not corrupt existing market rows.
3. Provide a safe initial data-fill path using a small allowlist.
4. Fetch and normalize low-frequency derivatives data for selected instruments.
5. Compute `oi_delta_1h`, `oi_delta_4h`, `funding_zscore`, `taker_buy_sell_ratio` or the closest available derivatives confirmation fields.
6. Replace fixed `derivatives_score` with a deterministic 0-100 score.
7. Extend `acts explain` to show derivatives fields and why the score moved.
8. Preserve graceful degradation when derivatives data is missing.

## Non-Goals

1. No order execution.
2. No private API keys.
3. No full WebSocket ingestion rewrite.
4. No full-market 31-day backfill by default.
5. No new database technology.
6. No machine-learning ranking.
7. No statistical backtest framework in this iteration.

## Data Backfill Design

### Kline Pagination

Each exchange adapter keeps its existing public `fetch_klines_1m` method, but the implementation should page internally until it covers the requested `[start_ms, end_ms]` window.

Behavior:

- Binance:
  - Use `/fapi/v1/klines`.
  - Keep interval `1m`.
  - Use `limit=1500`.
  - Advance the next request start to the last returned bar timestamp plus 60,000 ms.
- Bybit:
  - Use `/v5/market/kline`.
  - Keep interval `1`.
  - Use `limit=1000`.
  - Sort returned bars ascending before merging.
  - Advance the next request start to the last returned bar timestamp plus 60,000 ms.
- Stop when:
  - no rows are returned,
  - the last bar reaches or passes `end_ms`,
  - or the next start would not advance.

The parser should keep dropping malformed rows and preserving only finite numeric bars.

### Database Writes

`alt_core.market_1m` has primary key `(asset_id, ts)`. Current `insert_rows` emits plain `INSERT`, so rerunning bootstrap can fail on duplicate bars.

For this iteration, add an insert helper for market bars:

```text
insert_market_rows_ignore_conflicts(engine, rows)
```

It should insert into `alt_core.market_1m` with `ON CONFLICT (asset_id, ts) DO NOTHING` and return the number of rows accepted by PostgreSQL.

Bootstrap should use this helper for market bars only. Generic `insert_rows` should stay strict for snapshots and alert tables.

### Initial Backfill Scope

The first operational backfill should use a conservative allowlist:

```text
BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,LINKUSDT,AVAXUSDT,ARBUSDT,OPUSDT
```

Use 31 days initially. This is enough for relative strength and Binance open interest's documented latest-1-month availability.

## Derivatives Data Design

### Storage

Use existing nullable columns in `alt_core.market_1m` where practical:

- `open_interest`
- `funding_rate`
- `long_short_ratio`
- `buy_sell_ratio`

The backfill should align low-frequency derivatives observations to minute timestamps by writing values onto matching `market_1m` rows when the observation timestamp is available. It does not need to forward-fill every minute in the database during this iteration. The snapshot computation can use the latest available value in the lookback window.

If an exchange does not provide a field, leave it null.

### Fetching

Add adapter methods for public derivatives history:

```text
fetch_open_interest_history(symbol, start_ms, end_ms, period)
fetch_funding_rate_history(symbol, start_ms, end_ms)
fetch_long_short_ratio_history(symbol, start_ms, end_ms, period)
```

Return normalized dataclasses rather than raw exchange JSON.

Recommended periods:

- open interest: `1h` for storage and `4h` aggregation in features
- long/short ratio: `1h`
- funding rate: exchange funding interval, usually 8h

Bybit long/short ratio is available through `account-ratio`; Binance has separate long/short endpoints, but this iteration may implement Binance OI and funding first if long/short parsing would expand scope too much. Missing long/short data must not block the score.

### Feature Computation

For each asset in `build_snapshot_rows`, compute:

- `oi_delta_1h`: latest open interest versus roughly 1 hour earlier, percentage change.
- `oi_delta_4h`: latest open interest versus roughly 4 hours earlier, percentage change.
- `funding_zscore`: latest funding rate versus recent funding history.
- `taker_buy_sell_ratio`: existing taker buy quote versus total quote volume, if enough exchange data is present.

These values should populate existing columns in `alt_signal.feature_snapshot`:

- `oi_delta_1h`
- `oi_delta_4h`
- `funding_zscore`
- `taker_buy_sell_ratio`

### Derivatives Score

The score remains 0-100 with 50 as neutral.

Positive confirmation:

- Price is rising and open interest is rising over 1h or 4h.
- Taker buy participation is above neutral but not extreme.
- Funding is near neutral or only mildly positive.

Negative or cautionary confirmation:

- Price is rising while open interest falls, suggesting short-covering rather than new positioning.
- Funding z-score is extreme positive, suggesting crowded longs.
- Long/short ratio is extreme if available.
- Taker buy participation is extremely one-sided, suggesting late momentum.

Graceful fallback:

- No derivatives data: `derivatives_score = 50.0`.
- Only OI: score from OI/price confirmation and leave funding neutral.
- Only funding: score from funding crowding risk and leave OI neutral.

## Explain Output

`acts explain` should include a derivatives section:

```text
Derivatives:
OI delta 1h: ...
OI delta 4h: ...
Funding z-score: ...
Taker buy/sell ratio: ...
```

Missing values render as `n/a`.

## CLI Operations

The existing commands stay:

```bash
acts init-db
acts bootstrap --lookback-days 31
acts run-once
acts rank --limit 30
acts explain SOLUSDT --exchange binance
```

This iteration may add a small command or option for derivatives backfill if keeping it separate from kline bootstrap is cleaner:

```bash
acts bootstrap-derivatives --lookback-days 31
```

If added, it should use the same exchange and allowlist settings as normal bootstrap.

## Error Handling

The system must tolerate:

- partial exchange API failures,
- empty pages,
- duplicate market bars,
- insufficient derivatives history,
- missing BTC/ETH benchmark rows,
- allowlists that omit some derivative-capable symbols,
- exchange symbols that return klines but no OI or funding data.

Per-symbol failures should be reported and skipped where possible. One failing symbol should not invalidate the entire run unless the exchange instrument request itself fails.

## Testing Strategy

Use TDD for implementation.

Required tests:

1. Binance kline fetching paginates until the requested range is covered.
2. Bybit kline fetching paginates and returns bars sorted ascending.
3. Bootstrap uses conflict-tolerant market inserts.
4. Re-running bootstrap with duplicate bars does not fail.
5. Binance funding and OI parsers normalize public response rows.
6. Bybit funding, OI, and long/short parsers normalize public response rows.
7. Derivatives feature computation returns neutral score with no derivatives data.
8. Derivatives feature computation rewards price-up plus OI-up confirmation.
9. Derivatives feature computation penalizes price-up plus OI-down and overheated funding.
10. Snapshot rows populate derivatives fields and use non-fixed `derivatives_score` when data exists.
11. Explain output renders derivatives fields and `n/a` for missing values.
12. Full test suite continues to pass.

## Operational Verification

After implementation:

1. Ensure `altcoin_trend` database exists and migrations are applied.
2. Configure the initial allowlist.
3. Run `acts bootstrap --lookback-days 31`.
4. Run derivatives backfill if it is separate.
5. Run `acts run-once`.
6. Run `acts rank --limit 30`.
7. Run `acts explain` for at least one high-ranked symbol and one low-ranked symbol.

The final report should include:

- number of instruments selected,
- number of 1-minute bars written,
- date range in `alt_core.market_1m`,
- number of feature and rank rows,
- top 10 rank output,
- one explain output sample,
- any symbols skipped due to API or data limits.
