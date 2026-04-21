from types import SimpleNamespace

from altcoin_trend.signals.trade_candidate import (
    is_continuation_candidate,
    is_ignition_candidate,
    is_trade_candidate,
)


def _candidate_row(**overrides):
    row = {
        "return_1h_pct": 6.1,
        "return_4h_pct": 10.1,
        "return_24h_pct": 12.1,
        "volume_ratio_24h": 5.1,
        "return_24h_percentile": 0.95,
        "return_7d_percentile": 0.85,
        "quality_score": 100.0,
        "veto_reason_codes": [],
    }
    row.update(overrides)
    return row


def test_is_trade_candidate_accepts_iter25_style_momentum_breakout():
    assert is_trade_candidate(_candidate_row()) is True
    assert is_continuation_candidate(_candidate_row()) is True


def test_is_trade_candidate_rejects_missing_required_momentum_or_volume():
    assert is_trade_candidate(_candidate_row(return_1h_pct=5.9)) is False
    assert is_trade_candidate(_candidate_row(return_4h_pct=9.9)) is False
    assert is_trade_candidate(_candidate_row(return_24h_pct=11.9)) is False
    assert is_trade_candidate(_candidate_row(volume_ratio_24h=4.9)) is False


def test_is_trade_candidate_rejects_weak_relative_strength_quality_or_veto():
    assert is_trade_candidate(_candidate_row(return_24h_percentile=0.93)) is False
    assert is_trade_candidate(_candidate_row(return_7d_percentile=0.83)) is False
    assert is_trade_candidate(_candidate_row(quality_score=79.9)) is False
    assert is_trade_candidate(_candidate_row(veto_reason_codes=["risk"])) is False


def test_is_trade_candidate_accepts_object_rows():
    assert is_trade_candidate(SimpleNamespace(**_candidate_row())) is True


def _ignition_row(**overrides):
    row = {
        "return_1h_pct": 8.1,
        "return_24h_pct": 25.1,
        "return_24h_percentile": 0.93,
        "relative_strength_score": 86.0,
        "quality_score": 100.0,
        "volume_ratio_24h": 1.9,
        "volume_breakout_score": 20.0,
        "derivatives_score": 30.0,
        "veto_reason_codes": [],
    }
    row.update(overrides)
    return row


def test_is_ignition_candidate_accepts_today_breakout_without_7d_percentile_requirement():
    row = _ignition_row(return_7d_percentile=0.1)

    assert is_ignition_candidate(row) is True
    assert is_trade_candidate(row) is False


def test_is_ignition_candidate_accepts_volume_score_instead_of_raw_volume_ratio():
    row = _ignition_row(volume_ratio_24h=1.2, volume_breakout_score=35.0)

    assert is_ignition_candidate(row) is True


def test_is_ignition_candidate_rejects_weak_momentum_strength_derivatives_or_veto():
    assert is_ignition_candidate(_ignition_row(return_1h_pct=7.9)) is False
    assert is_ignition_candidate(_ignition_row(return_24h_pct=24.9)) is False
    assert is_ignition_candidate(_ignition_row(return_24h_percentile=0.91)) is False
    assert is_ignition_candidate(_ignition_row(relative_strength_score=84.9)) is False
    assert is_ignition_candidate(_ignition_row(quality_score=79.9)) is False
    assert is_ignition_candidate(_ignition_row(volume_ratio_24h=1.7, volume_breakout_score=34.9)) is False
    assert is_ignition_candidate(_ignition_row(derivatives_score=29.9)) is False
    assert is_ignition_candidate(_ignition_row(veto_reason_codes=["risk"])) is False
