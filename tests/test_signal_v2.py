from types import SimpleNamespace

from altcoin_trend.signals.v2 import (
    ULTRA_HIGH_CONVICTION_FLAG,
    compute_actionability_score,
    compute_chase_risk_score,
    compute_risk_flags,
    compute_volume_impulse_score,
    continuation_grade,
    evaluate_signal_v2,
    ignition_grade,
    is_top_24h,
    is_top_7d,
    signal_priority_for,
    ratio_score,
)


def test_ratio_score_uses_log_scale_and_clamps():
    assert ratio_score(None, full_at=5.0) == 0.0
    assert ratio_score(1.0, full_at=5.0) == 0.0
    assert ratio_score(5.0, full_at=5.0) == 100.0
    assert ratio_score(25.0, full_at=5.0) == 100.0


def test_compute_volume_impulse_score_weights_1h_4h_24h_and_breakout():
    row = {
        "volume_ratio_1h": 6.0,
        "volume_ratio_4h": 5.0,
        "volume_ratio_24h": 4.0,
        "breakout_20d": True,
    }

    assert compute_volume_impulse_score(row) == 100.0


def test_volume_impulse_treats_missing_1h_ratio_as_neutral():
    row = {"volume_ratio_4h": 5.0, "volume_ratio_24h": 4.0, "breakout_20d": False}

    assert compute_volume_impulse_score(row) == 60.0


def test_float_nan_values_are_treated_as_missing_for_volume_impulse():
    row = {"volume_ratio_1h": float("nan"), "volume_ratio_4h": 5.0, "volume_ratio_24h": 4.0}

    assert compute_volume_impulse_score(row) == 60.0


def test_top_return_helpers_accept_rank_or_percentile():
    assert is_top_24h({"return_24h_rank": 3, "return_24h_percentile": 0.10}, max_rank=3, min_percentile=0.94)
    assert is_top_24h({"return_24h_rank": 9, "return_24h_percentile": 0.95}, max_rank=3, min_percentile=0.94)
    assert not is_top_24h({"return_24h_rank": 4, "return_24h_percentile": 0.93}, max_rank=3, min_percentile=0.94)
    assert is_top_7d({"return_7d_rank": 5, "return_7d_percentile": 0.10}, max_rank=5, min_percentile=0.84)


def _continuation_row(**overrides):
    row = {
        "return_1h_pct": 6.1,
        "return_4h_pct": 10.1,
        "return_24h_pct": 12.1,
        "volume_ratio_24h": 5.1,
        "return_24h_rank": 3,
        "return_24h_percentile": 0.80,
        "return_7d_rank": 5,
        "return_7d_percentile": 0.80,
        "relative_strength_score": 86.0,
        "derivatives_score": 46.0,
        "volume_breakout_score": 51.0,
        "volume_impulse_score": 40.0,
        "quality_score": 100.0,
        "veto_reason_codes": [],
    }
    row.update(overrides)
    return row


def _ignition_row(**overrides):
    row = {
        "return_1h_pct": 8.1,
        "return_24h_pct": 25.1,
        "return_24h_rank": 3,
        "return_24h_percentile": 0.80,
        "relative_strength_score": 86.0,
        "quality_score": 100.0,
        "volume_ratio_24h": 1.9,
        "volume_impulse_score": 20.0,
        "volume_breakout_score": 20.0,
        "derivatives_score": 30.0,
        "veto_reason_codes": [],
    }
    row.update(overrides)
    return row


def _ultra_row(**overrides):
    row = {
        "return_1h_pct": 12.1,
        "return_4h_pct": 38.1,
        "return_24h_pct": 50.1,
        "return_30d_pct": 65.1,
        "volume_ratio_24h": 5.1,
        "return_24h_rank": 1,
        "return_24h_percentile": 0.999,
        "return_7d_rank": 5,
        "return_7d_percentile": 0.99,
        "return_30d_percentile": 0.81,
        "relative_strength_score": 80.0,
        "derivatives_score": 30.0,
        "volume_breakout_score": 40.0,
        "volume_impulse_score": 40.0,
        "quality_score": 100.0,
        "breakout_20d": True,
        "veto_reason_codes": [],
    }
    row.update(overrides)
    return row


def test_continuation_grade_splits_a_and_b_and_respects_veto():
    assert continuation_grade(_continuation_row()) == "A"
    assert continuation_grade(_continuation_row(derivatives_score=44.9)) == "B"
    assert continuation_grade(_continuation_row(return_1h_pct=5.9)) is None
    assert continuation_grade(_continuation_row(veto_reason_codes=["risk"])) is None


def test_continuation_grade_a_requires_breakout_or_impulse_confirmation_not_raw_ratio():
    row = _continuation_row(volume_ratio_24h=6.0, volume_breakout_score=49.9, volume_impulse_score=49.9)

    assert continuation_grade(row) == "B"


def test_none_items_inside_sequences_do_not_count_as_veto():
    assert continuation_grade(_continuation_row(veto_reason_codes=[None, ""])) == "A"


def test_ignition_grade_orders_extreme_before_a_before_b():
    assert ignition_grade(_ignition_row()) == "B"
    assert ignition_grade(
        _ignition_row(
            return_1h_pct=10.1,
            return_24h_pct=35.1,
            relative_strength_score=91.0,
            quality_score=86.0,
            volume_impulse_score=46.0,
            derivatives_score=35.0,
        )
    ) == "A"
    assert ignition_grade(
        _ignition_row(
            return_1h_pct=20.1,
            return_24h_pct=70.1,
            return_24h_percentile=0.95,
            relative_strength_score=91.0,
            volume_ratio_24h=1.6,
            derivatives_score=25.0,
        )
    ) == "EXTREME"


def test_ignition_grade_accepts_rank_without_percentile():
    row = _ignition_row(return_24h_percentile=None, return_24h_rank=3)

    assert ignition_grade(row) == "B"


def test_chase_risk_and_flags_mark_extreme_crowded_moves():
    row = _ignition_row(
        return_1h_pct=26.0,
        return_24h_pct=110.0,
        funding_zscore=2.6,
        taker_buy_sell_ratio=2.6,
        oi_delta_1h=-1.0,
        ignition_grade="EXTREME",
    )

    assert compute_chase_risk_score(row) == 100.0
    assert compute_risk_flags(row, ignition_grade="EXTREME", chase_risk_score=100.0) == (
        "EXTREME_MOVE",
        "CHASE_RISK",
        "FUNDING_OVERHEAT",
        "PRICE_UP_OI_DOWN",
        "TAKER_CROWDING",
        "EXTENDED_1H",
        "EXTENDED_24H",
    )


def test_risk_flags_do_not_fire_below_spec_thresholds():
    row = _ignition_row(
        return_1h_pct=24.9,
        return_24h_pct=99.9,
        funding_zscore=2.49,
        taker_buy_sell_ratio=2.49,
        oi_delta_1h=-1.0,
    )

    assert compute_risk_flags(row, ignition_grade=None, chase_risk_score=59.9) == ("PRICE_UP_OI_DOWN",)


def test_actionability_rewards_grade_confirmations_and_penalizes_risk():
    low_risk = _continuation_row(continuation_grade="A", cross_exchange_confirmed=True, volume_impulse_score=60.0)
    high_risk = dict(low_risk, chase_risk_score=80.0, risk_flags=["PRICE_UP_OI_DOWN"])

    assert compute_actionability_score(low_risk, continuation_grade="A", ignition_grade=None, risk_flags=(), chase_risk_score=0.0) > 60.0
    assert compute_actionability_score(high_risk, continuation_grade="A", ignition_grade=None, risk_flags=("PRICE_UP_OI_DOWN",), chase_risk_score=80.0) < 60.0


def test_signal_priority_uses_highest_priority_grade():
    assert signal_priority_for("A", "B") == 3
    assert signal_priority_for("B", "A") == 2
    assert signal_priority_for(None, "B") == 1


def test_evaluate_signal_v2_accepts_object_rows():
    result = evaluate_signal_v2(SimpleNamespace(**_continuation_row()))

    assert result.continuation_grade == "A"


def test_evaluate_signal_v2_returns_complete_result():
    result = evaluate_signal_v2(_continuation_row())

    assert result.continuation_grade == "A"
    assert result.ignition_grade is None
    assert result.ultra_high_conviction is False
    assert result.signal_priority == 3
    assert result.actionability_score > 0.0


def test_ultra_high_conviction_sets_flag_priority_and_actionability_bonus():
    result = evaluate_signal_v2(_ultra_row())
    baseline_actionability = compute_actionability_score(
        _ultra_row(),
        continuation_grade="B",
        ignition_grade=None,
        risk_flags=(),
        chase_risk_score=result.chase_risk_score,
    )

    assert result.continuation_grade == "B"
    assert result.ignition_grade is None
    assert result.ultra_high_conviction is True
    assert result.signal_priority == 3
    assert ULTRA_HIGH_CONVICTION_FLAG in result.risk_flags
    assert result.actionability_score > baseline_actionability
