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
