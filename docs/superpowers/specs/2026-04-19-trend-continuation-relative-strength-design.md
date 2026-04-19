# Trend Continuation and Relative Strength Signal Design

Date: 2026-04-19

## Summary

Improve the Altcoin Trend System so its rankings and alerts are driven by more useful trend continuation and market leadership signals. This iteration focuses on two dimensions that can be computed from the data already stored in `alt_core.market_1m`:

1. Trend continuation: identify assets with durable 4h/1d uptrend structure while penalizing overheated extensions.
2. Relative strength: rank assets by whether they are outperforming BTC and ETH over 7-day and 30-day windows.

The system remains a signal and alerting tool. It does not trade, does not add private exchange credentials, and does not add new external market data sources in this iteration.

## Current State

The existing pipeline can bootstrap Binance USD-M and Bybit linear USDT perpetual markets, store 1-minute bars, compute feature snapshots, rank assets, explain scores, and create Telegram alerts.

The current `relative_strength_score` is fixed at `50.0` inside the scheduler, and the trend score only partially uses higher-timeframe context. That means the ranking can identify basic trend and volume structure, but it does not yet distinguish true market leaders from assets that are only moving with the market.

## Goals

1. Make `relative_strength_score` data-driven instead of fixed.
2. Populate existing relative strength fields in `alt_signal.feature_snapshot`:
   - `rs_btc_7d`
   - `rs_eth_7d`
   - `rs_btc_30d`
   - `rs_eth_30d`
3. Improve trend continuation scoring using 4h/1d structure and extension risk.
4. Keep the existing rank and alert tables; do not add new alert types.
5. Extend `acts explain` so a user can see the relative strength values behind a score.
6. Preserve graceful degradation when BTCUSDT or ETHUSDT baseline data is missing.

## Non-Goals

1. No automated order execution.
2. No web UI or HTTP API.
3. No new data vendors or on-chain sources.
4. No WebSocket ingestion rewrite in this iteration.
5. No statistical backtest framework in this iteration.
6. No machine-learning ranker.
7. No database schema migration unless an implementation detail proves existing columns are insufficient.

## Data Flow

The pipeline continues to run through `run_once_pipeline`:

```text
alt_core.market_1m
  -> scheduler loads recent rows
  -> per-asset 4h/1d feature computation
  -> benchmark return computation for BTCUSDT and ETHUSDT
  -> trend continuation score + relative strength score
  -> alt_signal.feature_snapshot
  -> alt_signal.rank_snapshot
  -> existing alert state machine
```

The implementation should keep feature computation deterministic and local to dataframe transforms. It should not perform exchange API calls while computing snapshots.

## Trend Continuation Design

Trend continuation scoring should keep the existing 0-100 score contract and continue feeding `trend_score`.

Inputs:

- latest close
- 4h EMA20
- 4h EMA60
- 4h ADX14
- 1d EMA20
- 1d EMA60
- 7-day asset return
- 30-day asset return

Scoring behavior:

- Reward close above 4h EMA20.
- Reward 4h EMA20 above 4h EMA60.
- Reward 1d EMA20 above 1d EMA60 when enough data exists.
- Reward ADX14 strength up to a cap.
- Reward positive 7-day and 30-day returns.
- Penalize excessive extension above 4h EMA20 to avoid ranking late vertical moves too aggressively.

The extension penalty should be bounded. A strongly trending asset should not be rejected only because it is extended, but an asset far above its 4h EMA20 should lose enough points that fresher trend continuation candidates can outrank it.

## Relative Strength Design

Relative strength compares each asset's return against BTCUSDT and ETHUSDT on the same exchange when those benchmark rows are available.

For each asset:

```text
asset_return_7d = latest_close / close_7d_ago - 1
asset_return_30d = latest_close / close_30d_ago - 1
rs_btc_7d = asset_return_7d - btc_return_7d
rs_eth_7d = asset_return_7d - eth_return_7d
rs_btc_30d = asset_return_30d - btc_return_30d
rs_eth_30d = asset_return_30d - eth_return_30d
```

Values are stored as percentage points, not fractions. For example, if an asset is up 18% and BTC is up 5%, `rs_btc_7d` is `13.0`.

Relative strength scoring:

- 80-100: asset outperforms both BTC and ETH on 7-day and 30-day windows, with stronger weight on 7-day leadership.
- 60-80: asset has positive leadership on most windows or strong short-term leadership.
- 40-60: asset is close to benchmark performance or mixed.
- 20-40: asset underperforms most benchmark windows.
- 0-20: asset materially underperforms BTC and ETH.

The score should weight 7-day leadership more than 30-day leadership so the system reacts to current rotation, while still rewarding durable 30-day leadership.

## Baseline Fallback

When BTCUSDT or ETHUSDT data is missing for an exchange, the system must still produce a useful score:

1. Use whichever benchmark is available.
2. If neither benchmark is available, compute a cross-sectional fallback from all assets in the snapshot:
   - Compare each asset's 7-day and 30-day returns to the median return of the current universe.
   - Convert leadership over the median into a 0-100 score.
   - Leave unavailable benchmark-specific `rs_*` fields as `None`.

This keeps rankings useful in development databases that only contain a small allowlist of altcoins.

## Explain Output

`acts explain SYMBOL --exchange EXCHANGE` should include relative strength values when a feature snapshot exists:

```text
Relative strength:
RS vs BTC 7d: ...
RS vs ETH 7d: ...
RS vs BTC 30d: ...
RS vs ETH 30d: ...
```

Missing values should render as `n/a`, not as zero. Zero has a real meaning: exactly matching benchmark performance.

## Alert Behavior

The existing alert types remain unchanged:

- `strong_trend`
- `watchlist_enter`
- `breakout_confirmed`
- `risk_downgrade`

Because `final_score` already includes `relative_strength_score` at 20% weight, better relative strength scoring will naturally affect which assets enter `strong` or `watchlist`. This iteration should not add a separate "relative strength" alert, because that would increase noise before the basic score is proven useful.

## Error Handling

Feature computation must tolerate:

- insufficient 7-day or 30-day history
- missing BTCUSDT or ETHUSDT baselines
- assets with zero or invalid historical close values
- single-exchange datasets
- small allowlisted datasets

In those cases, the system should degrade to the strongest available comparison instead of failing the entire snapshot.

## Testing Strategy

Use TDD for implementation.

Required tests:

1. Relative strength math computes percentage-point outperformance against BTC and ETH.
2. Relative strength scoring rewards assets that outperform both benchmarks.
3. Relative strength scoring penalizes assets that underperform both benchmarks.
4. Missing benchmark data uses the cross-sectional fallback without crashing.
5. Snapshot rows include `rs_btc_7d`, `rs_eth_7d`, `rs_btc_30d`, `rs_eth_30d`, and a non-fixed `relative_strength_score`.
6. Explain output renders relative strength values and uses `n/a` for missing values.
7. Existing ranking, scoring, alert, and CLI tests continue to pass.

## Implementation Boundary

Expected files to change:

- `src/altcoin_trend/features/relative_strength.py`
- `src/altcoin_trend/features/trend.py` if a dedicated trend helper is useful
- `src/altcoin_trend/scheduler.py`
- `src/altcoin_trend/signals/explain.py`
- targeted tests under `tests/`

Avoid touching exchange adapters, database migrations, Telegram delivery, and systemd service files unless tests expose a direct need.
