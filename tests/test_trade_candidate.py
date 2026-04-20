from types import SimpleNamespace

from altcoin_trend.signals.trade_candidate import is_trade_candidate


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
