from altcoin_trend.signals.state import AlertDecision, evaluate_transition


def test_watchlist_to_strong_emits_strong_trend_when_breakout_and_oi_confirmed():
    decision = evaluate_transition(
        previous_tier="watchlist",
        current_tier="strong",
        breakout_confirmed=True,
        oi_confirmed=True,
        veto_reason_codes=(),
    )

    assert decision == AlertDecision(alert_type="strong_trend", should_alert=True)


def test_watchlist_to_rejected_with_veto_emits_risk_downgrade():
    decision = evaluate_transition(
        previous_tier="watchlist",
        current_tier="rejected",
        breakout_confirmed=False,
        oi_confirmed=False,
        veto_reason_codes=("volume_breakout_low",),
    )

    assert decision == AlertDecision(alert_type="risk_downgrade", should_alert=True)


def test_monitor_to_monitor_without_breakout_oi_or_veto_is_silent():
    decision = evaluate_transition(
        previous_tier="monitor",
        current_tier="monitor",
        breakout_confirmed=False,
        oi_confirmed=False,
        veto_reason_codes=(),
    )

    assert decision.should_alert is False
    assert decision.alert_type == ""
