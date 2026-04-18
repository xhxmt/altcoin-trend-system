CREATE SCHEMA IF NOT EXISTS alt_raw;

CREATE TABLE IF NOT EXISTS alt_raw.exchange_messages (
    message_id BIGSERIAL PRIMARY KEY,
    exchange TEXT NOT NULL,
    stream_name TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS alt_raw.rest_fetch_runs (
    fetch_id BIGSERIAL PRIMARY KEY,
    exchange TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    symbol TEXT,
    range_start TIMESTAMPTZ,
    range_end TIMESTAMPTZ,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    row_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS alt_raw.ws_connection_events (
    event_id BIGSERIAL PRIMARY KEY,
    exchange TEXT NOT NULL,
    connection_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);
