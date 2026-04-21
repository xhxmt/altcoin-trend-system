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
