CREATE SCHEMA IF NOT EXISTS alt_ingest;
CREATE SCHEMA IF NOT EXISTS alt_core;

CREATE TABLE IF NOT EXISTS alt_ingest.daemon_runs (
    run_id TEXT PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    error_message TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS alt_ingest.bootstrap_runs (
    run_id TEXT PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    exchange TEXT NOT NULL,
    lookback_days INTEGER NOT NULL,
    status TEXT NOT NULL,
    rows_written INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS alt_ingest.repair_runs (
    run_id TEXT PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    asset_id BIGINT,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    range_start TIMESTAMPTZ NOT NULL,
    range_end TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    rows_written INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS alt_core.asset_master (
    asset_id BIGSERIAL PRIMARY KEY,
    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    base_asset TEXT NOT NULL,
    quote_asset TEXT NOT NULL,
    status TEXT NOT NULL,
    onboard_at TIMESTAMPTZ,
    contract_type TEXT,
    tick_size DOUBLE PRECISION,
    step_size DOUBLE PRECISION,
    min_notional DOUBLE PRECISION,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (exchange, market_type, symbol)
);

CREATE TABLE IF NOT EXISTS alt_core.market_1m (
    ts TIMESTAMPTZ NOT NULL,
    asset_id BIGINT NOT NULL REFERENCES alt_core.asset_master(asset_id),
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    quote_volume DOUBLE PRECISION NOT NULL,
    trade_count BIGINT,
    taker_buy_base DOUBLE PRECISION,
    taker_buy_quote DOUBLE PRECISION,
    open_interest DOUBLE PRECISION,
    funding_rate DOUBLE PRECISION,
    long_short_ratio DOUBLE PRECISION,
    buy_sell_ratio DOUBLE PRECISION,
    data_status TEXT NOT NULL CHECK (data_status IN ('healthy', 'partial', 'stale')),
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (asset_id, ts)
);

CREATE TABLE IF NOT EXISTS alt_core.market_bar (
    ts TIMESTAMPTZ NOT NULL,
    asset_id BIGINT NOT NULL REFERENCES alt_core.asset_master(asset_id),
    timeframe TEXT NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    quote_volume DOUBLE PRECISION NOT NULL,
    trade_count BIGINT,
    taker_buy_base DOUBLE PRECISION,
    taker_buy_quote DOUBLE PRECISION,
    open_interest DOUBLE PRECISION,
    funding_rate DOUBLE PRECISION,
    long_short_ratio DOUBLE PRECISION,
    buy_sell_ratio DOUBLE PRECISION,
    data_status TEXT NOT NULL CHECK (data_status IN ('healthy', 'partial', 'stale')),
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (asset_id, timeframe, ts)
);
