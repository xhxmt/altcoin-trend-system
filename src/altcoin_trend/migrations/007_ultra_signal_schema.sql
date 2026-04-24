ALTER TABLE alt_signal.feature_snapshot
    ADD COLUMN IF NOT EXISTS return_30d_percentile DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS return_30d_rank INTEGER,
    ADD COLUMN IF NOT EXISTS ultra_high_conviction BOOLEAN NOT NULL DEFAULT FALSE;

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
        'ultra_high_conviction',
        'exhaustion_risk'
    ));
