# Altcoin Trend System MVP Design

Date: 2026-04-18

## Summary

Build a separate minute-to-hour altcoin trend detection and ranking system at `/home/tfisher/altcoin-trend-system`.

The MVP scans Binance USD-M and Bybit linear USDT perpetual contracts. It uses public exchange market data to detect rising trends, rank candidates, and send Telegram alerts. It does not trade, does not store exchange API secrets, and does not connect to DEX, BSC, Solana, CoinGecko, DefiLlama, Dune, Messari, or ML ranking in the first version.

The project is a clean-room system. It may follow the engineering style of `/home/tfisher/btc-trading-system`, but it must not reuse the `btc_research_core` package or inherit BTCUSDT-only assumptions.

## Goals

1. Discover USDT perpetual contracts that are already in, or are forming, an upward trend.
2. Rank candidates by trend strength, breakout quality, relative strength, derivatives confirmation, and data quality.
3. Alert through Telegram when a symbol enters a strong trend, enters the watchlist, confirms a breakout, or suffers a risk downgrade.
4. Provide CLI commands for rank inspection, daemon status, recent alerts, and per-symbol score explanation.
5. Store normalized market data, feature snapshots, rank snapshots, and alert events in PostgreSQL/TimescaleDB.

## Non-Goals

1. No order execution.
2. No private exchange API keys.
3. No spot market scanning in the MVP.
4. No DEX Screener, CoinGecko, DefiLlama, BSC RPC, Solana RPC, Helius, Dune, or Messari in the MVP.
5. No web UI or HTTP API server.
6. No machine-learning ranking in the MVP.
7. No shared library extraction from the BTC system during the MVP.

## Project Boundary

The new system lives in:

```text
/home/tfisher/altcoin-trend-system
```

Naming:

```text
Python package: altcoin_trend
CLI command: acts
systemd service: altcoin-trend.service
PostgreSQL schemas: alt_ingest, alt_raw, alt_core, alt_signal
Environment prefix: ACTS_
```

The current BTC system continues to live in:

```text
/home/tfisher/btc-trading-system
```

The new system must not modify the current BTC daemon or its systemd service.

## Architecture

Runtime flow:

```text
[Binance WS/REST]   [Bybit WS/REST]
        |                  |
        v                  v
[exchange adapters + rate limiters]
        |
        v
[normalization + PostgreSQL storage]
        |
        v
[1m close scheduler]
        |
        v
[local resampling: 5m/15m/1h/4h/1d]
        |
        v
[feature engine]
        |
        v
[ranking + signal state machine]
        |
        v
[rank snapshots] + [Telegram alerts] + [CLI explain/status]
```

The daemon uses REST initialization, WebSocket live updates, and REST gap repair:

1. Bootstrap instruments, symbol metadata, recent 1m history, and low-frequency derivatives data.
2. Subscribe to WebSocket ticker/kline streams for live updates.
3. Repair missing 1m bars after disconnects, restarts, or out-of-order messages.
4. Resample all higher timeframes locally from `market_1m`.
5. Compute features and ranking snapshots every confirmed 1m close.
6. Evaluate signal transitions and send Telegram alerts with cooldown.

## External API Constraints

The implementation must treat exchange API constraints as first-class design requirements.

Binance USD-M Futures:

1. Use the current WebSocket path strategy rather than legacy-only assumptions. Binance's 2026-04-02 change log entry updated the legacy WebSocket URL decommissioning date to 2026-04-23.
2. A single market stream connection is valid for 24 hours.
3. A connection has a 10 incoming messages/second limit.
4. A single connection can listen to at most 1024 streams.
5. Symbols in stream names are lowercase.

Bybit:

1. Implement a separate v5 rate limiter.
2. Keep Bybit stream names, payload parsing, instrument metadata, funding intervals, and REST pagination separate from Binance.
3. Do not share Binance rate-limit assumptions with Bybit.

## Database Design

Use PostgreSQL/TimescaleDB. The MVP schema is separated into four logical layers.

### `alt_ingest`

Tracks ingestion, bootstrap, repair, and daemon runs.

Important tables:

```text
alt_ingest.daemon_runs
alt_ingest.bootstrap_runs
alt_ingest.repair_runs
```

### `alt_raw`

Stores raw or near-raw exchange observations needed for replay and debugging.

Important tables:

```text
alt_raw.exchange_messages
alt_raw.rest_fetch_runs
alt_raw.ws_connection_events
```

`exchange_messages` is not required to retain every WebSocket payload forever. Retention can be configurable. Standardized market data is the long-term analytical source of truth.

### `alt_core.asset_master`

Exchange-specific instrument registry:

```text
asset_id
exchange
market_type
symbol
base_asset
quote_asset
status
onboard_at
contract_type
tick_size
step_size
min_notional
is_enabled
created_at
updated_at
```

The MVP keeps Binance `SOLUSDT` and Bybit `SOLUSDT` as separate `asset_id` values. Cross-exchange display can group by `base_asset`, but storage remains exchange-specific to avoid mixing contract rules and funding schedules.

### `alt_core.market_1m`

Primary normalized minute table:

```text
ts
asset_id
exchange
symbol
open
high
low
close
volume
quote_volume
trade_count
taker_buy_base
taker_buy_quote
open_interest
funding_rate
long_short_ratio
buy_sell_ratio
data_status
reason_codes
```

`data_status` values are:

```text
healthy
partial
stale
```

Low-frequency derivatives values are forward-filled only while fresh enough. Stale values must degrade `data_status` or add `reason_codes`.

### `alt_core.market_bar`

Higher timeframe bars generated locally from `market_1m`:

```text
ts
asset_id
timeframe
open
high
low
close
volume
quote_volume
trade_count
taker_buy_base
taker_buy_quote
open_interest
funding_rate
long_short_ratio
buy_sell_ratio
data_status
reason_codes
```

Supported MVP timeframes:

```text
5m
15m
1h
4h
1d
```

### `alt_signal.feature_snapshot`

Feature and score table:

```text
ts
asset_id
exchange
symbol
close
ema20_1m
ema20_4h
ema60_4h
ema20_1d
ema60_1d
adx14_4h
atr14_4h
volume_ratio_4h
breakout_20d
rs_btc_7d
rs_eth_7d
rs_btc_30d
rs_eth_30d
oi_delta_1h
oi_delta_4h
funding_zscore
taker_buy_sell_ratio
trend_score
volume_breakout_score
relative_strength_score
derivatives_score
quality_score
final_score
veto_reason_codes
```

### `alt_signal.rank_snapshot`

Query-optimized ranking output:

```text
ts
rank_scope
rank
asset_id
symbol
base_asset
final_score
tier
primary_reason
payload
```

Supported `rank_scope` values:

```text
all
binance
bybit
```

Supported `tier` values:

```text
strong
watchlist
monitor
rejected
```

### `alt_signal.alert_events`

Alert history and delivery status:

```text
alert_id
ts
asset_id
symbol
alert_type
final_score
message
payload
delivery_status
delivery_error
```

Supported `alert_type` values:

```text
strong_trend
watchlist_enter
breakout_confirmed
risk_downgrade
```

Supported `delivery_status` values:

```text
pending
sent
failed
suppressed
```

## Exchange Adapters

Adapters live under:

```text
src/altcoin_trend/exchanges/
```

Each adapter exposes this internal interface:

```text
list_usdt_perp_symbols()
fetch_klines_1m(symbol, start, end)
fetch_open_interest(symbol, start, end)
fetch_funding(symbol, start, end)
fetch_long_short_ratio(symbol, start, end)
connect_ticker_stream(symbols)
connect_kline_stream(symbols)
```

Adapter internals are exchange-specific. Field parsing, endpoint pagination, funding interval semantics, WebSocket stream names, and rate limits must not be forced through a single universal parser.

## Symbol Pool

The MVP scans USDT perpetual contracts after filters:

```text
quote_asset == USDT
market_type == usdt_perp
status == trading
listing age > ACTS_MIN_LISTING_DAYS
24h quote volume > ACTS_MIN_QUOTE_VOLUME_24H
recent 7d 1m gap rate below configured threshold
not in ACTS_SYMBOL_BLOCKLIST
in ACTS_SYMBOL_ALLOWLIST when allowlist is set
```

Defaults:

```text
ACTS_MIN_LISTING_DAYS=60
ACTS_MIN_QUOTE_VOLUME_24H=5000000
```

## Gap Repair

Each asset tracks its latest confirmed `market_1m` close. When a WebSocket kline arrives after a gap:

```text
expected_next_ts = last_closed_ts + 1 minute

if ws_closed_kline.ts > expected_next_ts:
    fetch_klines_1m(symbol, expected_next_ts, ws_closed_kline.ts - 1 minute)
```

If repair succeeds, rows are written as `healthy` or `partial`. If repair fails, downstream bars and features must be marked `stale` or `partial` with reason codes. Missing data must not be silently ignored.

## Feature and Scoring Design

The MVP detects long-side upward trend only. It does not produce short signals.

Score groups:

### TrendScore

Inputs:

```text
1d close > ema20 > ema60
1d ema20 slope > 0
1d ema60 slope > 0
4h close > ema20 > ema60
4h higher high and higher low
4h adx14 > 23
```

### VolumeBreakoutScore

Inputs:

```text
4h volume / 20-bar average volume
close > 20d high
close > recent range upper bound
distance_to_ema20_4h / atr14_4h not overheated
```

### RelativeStrengthScore

Inputs:

```text
rs_btc_7d = coin_ret_7d - btc_ret_7d
rs_eth_7d = coin_ret_7d - eth_ret_7d
rs_btc_30d = coin_ret_30d - btc_ret_30d
rs_eth_30d = coin_ret_30d - eth_ret_30d
cross-sectional percentile rank
```

BTC and ETH benchmark returns are computed from the same exchange when available. If unavailable, Binance benchmark data is the fallback.

### DerivativesScore

Inputs:

```text
price_up + oi_up
funding > 0 but not extreme
taker_buy_sell_ratio > 1
long_short_ratio as auxiliary evidence only
```

### QualityScore

Inputs:

```text
data completeness
quote volume
funding crowding
distance from ema20_4h
abnormal 24h volatility without volume confirmation
```

MVP weights:

```text
FinalScore =
0.35 * TrendScore
+ 0.25 * VolumeBreakoutScore
+ 0.20 * RelativeStrengthScore
+ 0.15 * DerivativesScore
+ 0.05 * QualityScore
```

Tiers:

```text
85+      strong
75-84    watchlist
60-74    monitor
<60      rejected
```

## Veto Rules

Veto rules override the numeric score:

```text
recent data gap rate above threshold
24h quote volume below threshold
funding extreme
price distance from ema20_4h > 2.5 * atr14_4h
24h price move is extreme but 4h volume did not expand
```

Vetoed symbols are `rejected` or downgraded with explicit `veto_reason_codes`.

## Signal State Machine

Maintain state per `(exchange, symbol)`:

```text
rejected -> monitor -> watchlist -> strong
strong -> watchlist -> monitor -> rejected
```

Alert triggers:

```text
strong_trend:
  previous_tier != strong
  and current_tier == strong
  and breakout_confirmed
  and oi_confirmed
  and no veto

watchlist_enter:
  previous_tier below watchlist
  and current_tier == watchlist
  and relative_strength_positive
  and volume_expanding

breakout_confirmed:
  close crosses 20d high or range upper
  and volume_ratio_4h > threshold

risk_downgrade:
  prior tier was strong/watchlist
  and current tier is lower or veto appears
```

Deduplication:

```text
same symbol + alert_type cooldown: 4h by default
tier upgrade can bypass cooldown
risk_downgrade can bypass cooldown
daemon restart restores recent alert history from alt_signal.alert_events
```

## Telegram Alerts

Telegram uses:

```text
ACTS_TELEGRAM_BOT_TOKEN
ACTS_TELEGRAM_CHAT_ID
```

Message types:

```text
strong trend alert
watchlist summary
risk downgrade alert
```

Example:

```text
[STRONG] SOLUSDT Binance
Score: 88.4
Trend 31/35 | Volume 21/25 | RS 16/20 | Deriv 12/15 | Quality 5/5
Reasons: 20d breakout, OI +8.3% 4h, RS_BTC_7D +12.1%
Risk: funding warm, distance 1.8 ATR
```

Delivery failures update `alt_signal.alert_events.delivery_status` and `delivery_error`. Telegram failures must not crash the daemon.

## CLI Design

Commands:

```text
acts init-db
acts bootstrap --lookback-days 90
acts run-once
acts daemon
acts rank --limit 30
acts rank --exchange binance
acts status
acts alerts --since 24h
acts explain SYMBOL --exchange binance
```

`acts explain` must output:

```text
final_score
tier
score group breakdown
reason codes
veto reason codes
data freshness
latest relevant market values
```

## Configuration

Example env file:

```text
ACTS_DATABASE_URL=postgresql+psycopg://tfisher@/altcoin_trend
ACTS_OUTPUT_ROOT=/home/tfisher/altcoin-trend-system/artifacts
ACTS_DEFAULT_EXCHANGES=binance,bybit
ACTS_QUOTE_ASSET=USDT
ACTS_MIN_QUOTE_VOLUME_24H=5000000
ACTS_MIN_LISTING_DAYS=60
ACTS_BOOTSTRAP_LOOKBACK_DAYS=90
ACTS_SIGNAL_INTERVAL_SECONDS=60
ACTS_ALERT_COOLDOWN_SECONDS=14400
ACTS_TELEGRAM_BOT_TOKEN=
ACTS_TELEGRAM_CHAT_ID=
ACTS_SYMBOL_ALLOWLIST=
ACTS_SYMBOL_BLOCKLIST=
```

User config path:

```text
~/.config/acts/acts.env
```

## systemd Design

Service path in the repo:

```text
systemd/user/altcoin-trend.service
```

Installed user service path:

```text
~/.config/systemd/user/altcoin-trend.service
```

Expected service body:

```text
[Unit]
Description=Altcoin Trend System Daemon
After=default.target

[Service]
Type=simple
WorkingDirectory=/home/tfisher/altcoin-trend-system
EnvironmentFile=%h/.config/acts/acts.env
Environment=PYTHONPATH=/home/tfisher/altcoin-trend-system/src
ExecStart=/home/tfisher/altcoin-trend-system/.venv/bin/python -m altcoin_trend.daemon
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

## Proposed Project Structure

```text
/home/tfisher/altcoin-trend-system/
  pyproject.toml
  README.md
  config/
    acts.env.example
  sql/
    001_core_schema.sql
    002_raw_exchange.sql
    003_signal_schema.sql
  systemd/user/
    altcoin-trend.service
  docs/superpowers/specs/
    2026-04-18-altcoin-trend-system-mvp-design.md
  src/altcoin_trend/
    __init__.py
    cli.py
    config.py
    db.py
    daemon.py
    scheduler.py
    models.py
    exchanges/
      __init__.py
      base.py
      binance.py
      bybit.py
      rate_limit.py
      ws.py
    ingest/
      bootstrap.py
      live.py
      repair.py
      normalize.py
    features/
      indicators.py
      resample.py
      trend.py
      volume.py
      relative_strength.py
      derivatives.py
      quality.py
      scoring.py
    signals/
      ranking.py
      state.py
      alerts.py
      telegram.py
      explain.py
  tests/
    test_config.py
    test_resample.py
    test_indicators.py
    test_scoring.py
    test_state.py
    test_alerts.py
    test_exchange_contracts.py
```

## Dependencies

MVP dependencies:

```text
typer
pydantic-settings
sqlalchemy
psycopg[binary]
pandas
numpy
httpx
websockets
pytest
```

## Testing Strategy

Required tests:

```text
config loading and env overrides
exchange payload parsing using fixture messages
rate limiter budget behavior
1m to higher timeframe resampling
indicator math
score group calculations
veto precedence over final score
tier transition rules
alert cooldown and bypass behavior
Telegram failure handling
CLI explain output shape
```

Integration checks:

```text
acts init-db creates schemas and tables
acts bootstrap writes asset_master and sample market rows
acts run-once writes feature_snapshot and rank_snapshot
acts explain prints score breakdown for a fixture-backed symbol
systemd service file points to /home/tfisher/altcoin-trend-system
```

## Acceptance Criteria

The MVP is complete when:

1. `pytest` passes.
2. `acts init-db` creates the database schema.
3. `acts bootstrap --lookback-days 90` can populate asset metadata and recent 1m data for a configured symbol subset.
4. `acts run-once` can create feature and rank snapshots.
5. `acts rank --limit 30` shows ranked candidates.
6. `acts explain SYMBOL --exchange binance` explains score components and veto reasons.
7. Telegram test delivery works when token/chat id are configured.
8. The daemon can run under `altcoin-trend.service` and report healthy or degraded status.

## Implementation Sequencing

The implementation plan should split work into these phases:

1. Project skeleton, config, database, CLI, and tests.
2. Exchange adapter contracts and fixture-backed parsers.
3. REST bootstrap for instruments and 1m history.
4. Local resampling and indicator math.
5. Feature scoring, veto rules, ranking snapshots, and explain output.
6. Signal state machine and Telegram alerts.
7. WebSocket live ingestion and gap repair.
8. Daemon loop, status reporting, systemd service, and end-to-end verification.

## References

Primary source references used while preparing this design:

1. Binance USD-M Futures change log, especially 2026-04-02 WebSocket URL decommissioning update.
2. Binance USD-M Futures WebSocket market streams documentation for 24h connection lifetime, 10 incoming messages/second limit, and 1024 stream limit.
3. Bybit v5 rate limit documentation.
