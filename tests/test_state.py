import pytest

from altcoin_trend.signals.state import AlertDecision, evaluate_transition


@pytest.mark.parametrize(
    ("previous_tier", "current_tier", "breakout_confirmed", "oi_confirmed", "veto_reason_codes", "expected"),
    [
        (
            "rejected",
            "watchlist",
            False,
            False,
            (),
            AlertDecision(alert_type="watchlist_enter", should_alert=True),
        ),
        (
            "monitor",
            "strong",
            True,
            True,
            (),
            AlertDecision(alert_type="strong_trend", should_alert=True),
        ),
        (
            "watchlist",
            "rejected",
            False,
            False,
            (),
            AlertDecision(alert_type="risk_downgrade", should_alert=True),
        ),
        (
            "strong",
            "strong",
            True,
            True,
            ("funding_heat",),
            AlertDecision(alert_type="risk_downgrade", should_alert=True),
        ),
        (
            "monitor",
            "monitor",
            True,
            False,
            (),
            AlertDecision(alert_type="breakout_confirmed", should_alert=True),
        ),
        (
            "monitor",
            "monitor",
            False,
            False,
            (),
            AlertDecision(alert_type="", should_alert=False),
        ),
    ],
)
def test_evaluate_transition_matrix_covers_required_branches(
    previous_tier,
    current_tier,
    breakout_confirmed,
    oi_confirmed,
    veto_reason_codes,
    expected,
):
    assert (
        evaluate_transition(
            previous_tier=previous_tier,
            current_tier=current_tier,
            breakout_confirmed=breakout_confirmed,
            oi_confirmed=oi_confirmed,
            veto_reason_codes=veto_reason_codes,
        )
        == expected
    )


def test_veto_suppresses_positive_alerts_and_risk_downgrade_takes_precedence():
    decision = evaluate_transition(
        previous_tier="watchlist",
        current_tier="strong",
        breakout_confirmed=True,
        oi_confirmed=True,
        veto_reason_codes=("volume_breakout_low",),
    )

    assert decision == AlertDecision(alert_type="risk_downgrade", should_alert=True)
