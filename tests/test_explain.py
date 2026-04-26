from altcoin_trend.signals.explain import build_explain_text


def test_build_explain_text_surfaces_signal_v2_fields():
    text = build_explain_text(
        {
            "exchange": "binance",
            "symbol": "RAVEUSDT",
            "final_score": 88.5,
            "tier": "watchlist",
            "signal_priority": 2,
            "continuation_grade": None,
            "ignition_grade": "",
            "reacceleration_grade": "B",
            "ultra_high_conviction": False,
            "actionability_score": 71.25,
            "chase_risk_score": 20.0,
            "risk_flags": ["ULTRA_HIGH_CONVICTION", None, "PRICE_UP_OI_DOWN"],
            "trend_score": 80.0,
            "volume_breakout_score": 74.7,
            "relative_strength_score": 91.0,
            "derivatives_score": 42.0,
            "quality_score": 95.0,
            "veto_reason_codes": [None, ""],
        }
    )

    assert "binance:RAVEUSDT" in text
    assert "Signal v2:" in text
    assert "Priority: 2" in text
    assert "Continuation: -" in text
    assert "Ignition: -" in text
    assert "Reacceleration: B" in text
    assert "Ultra high conviction: no" in text
    assert "Actionability: 71.25" in text
    assert "Chase risk: 20.00" in text
    assert "Risk flags: ULTRA_HIGH_CONVICTION, PRICE_UP_OI_DOWN" in text
    assert "Veto: none" in text


def test_build_explain_text_explains_early_volume_reacceleration_b():
    text = build_explain_text(
        {
            "exchange": "binance",
            "symbol": "RAVEUSDT",
            "final_score": 88.5,
            "tier": "watchlist",
            "signal_priority": 1,
            "continuation_grade": None,
            "ignition_grade": None,
            "reacceleration_grade": "B",
            "actionability_score": 71.25,
            "chase_risk_score": 12.0,
            "risk_flags": [],
            "return_1h_pct": 3.4,
            "return_4h_pct": 8.2,
            "return_24h_pct": 20.0,
            "volume_ratio_24h": 3.2,
            "volume_breakout_score": 92.0,
            "return_24h_percentile": 0.82,
            "return_7d_percentile": 0.91,
            "return_30d_percentile": 0.86,
            "breakout_20d": True,
            "trend_score": 91.0,
            "relative_strength_score": 88.0,
            "quality_score": 94.0,
            "oi_delta_1h": 5.0,
            "oi_delta_4h": 14.0,
            "veto_reason_codes": [],
        }
    )

    assert "Reacceleration details:" in text
    assert "Branch: early-volume B" in text
    assert "Early-volume B gates: pass" in text
    assert "Classic B gates: fail" in text
    assert "return_24h_pct 20.00 < 30.00" in text
    assert "Why not A: return_24h_pct 20.00 < 60.00" in text
    assert "Drivers: OI, relative strength, trend, volume" in text
    assert "Chase suppression: no (12.00 <= 40.00)" in text


def test_build_explain_text_handles_missing_signal_v2_fields():
    text = build_explain_text(
        {
            "exchange": "bybit",
            "symbol": "SOLUSDT",
            "final_score": 42.0,
            "tier": "rejected",
            "veto_reason_codes": "STALE_MARKET",
        }
    )

    assert "Priority: n/a" in text
    assert "Continuation: -" in text
    assert "Ignition: -" in text
    assert "Reacceleration: -" in text
    assert "Ultra high conviction: no" in text
    assert "Actionability: n/a" in text
    assert "Chase risk: n/a" in text
    assert "Risk flags: none" in text
    assert "Veto: STALE_MARKET" in text
