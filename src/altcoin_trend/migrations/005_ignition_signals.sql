ALTER TABLE alt_signal.feature_snapshot
    ADD COLUMN IF NOT EXISTS continuation_candidate BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS ignition_candidate BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE alt_signal.alert_events
    DROP CONSTRAINT IF EXISTS alert_events_alert_type_check;

ALTER TABLE alt_signal.alert_events
    ADD CONSTRAINT alert_events_alert_type_check
    CHECK (alert_type IN (
        'strong_trend',
        'watchlist_enter',
        'breakout_confirmed',
        'risk_downgrade',
        'explosive_move_early'
    ));
