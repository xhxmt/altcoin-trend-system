CREATE SCHEMA IF NOT EXISTS alt_signal;

CREATE TABLE IF NOT EXISTS alt_signal.feature_snapshot (
    ts TIMESTAMPTZ NOT NULL,
    asset_id BIGINT NOT NULL REFERENCES alt_core.asset_master(asset_id),
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    ema20_1m DOUBLE PRECISION,
    ema20_4h DOUBLE PRECISION,
    ema60_4h DOUBLE PRECISION,
    ema20_1d DOUBLE PRECISION,
    ema60_1d DOUBLE PRECISION,
    adx14_4h DOUBLE PRECISION,
    atr14_4h DOUBLE PRECISION,
    volume_ratio_4h DOUBLE PRECISION,
    breakout_20d BOOLEAN NOT NULL DEFAULT FALSE,
    rs_btc_7d DOUBLE PRECISION,
    rs_eth_7d DOUBLE PRECISION,
    rs_btc_30d DOUBLE PRECISION,
    rs_eth_30d DOUBLE PRECISION,
    oi_delta_1h DOUBLE PRECISION,
    oi_delta_4h DOUBLE PRECISION,
    funding_zscore DOUBLE PRECISION,
    taker_buy_sell_ratio DOUBLE PRECISION,
    trend_score DOUBLE PRECISION NOT NULL,
    volume_breakout_score DOUBLE PRECISION NOT NULL,
    relative_strength_score DOUBLE PRECISION NOT NULL,
    derivatives_score DOUBLE PRECISION NOT NULL,
    quality_score DOUBLE PRECISION NOT NULL,
    final_score DOUBLE PRECISION NOT NULL,
    veto_reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (asset_id, ts)
);

CREATE TABLE IF NOT EXISTS alt_signal.rank_snapshot (
    ts TIMESTAMPTZ NOT NULL,
    rank_scope TEXT NOT NULL,
    rank INTEGER NOT NULL,
    asset_id BIGINT NOT NULL REFERENCES alt_core.asset_master(asset_id),
    symbol TEXT NOT NULL,
    base_asset TEXT NOT NULL,
    final_score DOUBLE PRECISION NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('strong', 'watchlist', 'monitor', 'rejected')),
    primary_reason TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (ts, rank_scope, rank)
);

CREATE TABLE IF NOT EXISTS alt_signal.alert_events (
    alert_id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    asset_id BIGINT NOT NULL REFERENCES alt_core.asset_master(asset_id),
    symbol TEXT NOT NULL,
    alert_type TEXT NOT NULL CHECK (alert_type IN ('strong_trend', 'watchlist_enter', 'breakout_confirmed', 'risk_downgrade')),
    final_score DOUBLE PRECISION NOT NULL,
    message TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    delivery_status TEXT NOT NULL CHECK (delivery_status IN ('pending', 'sent', 'failed', 'suppressed')),
    delivery_error TEXT
);
