from dataclasses import fields

from altcoin_trend.features.scoring import ScoreInput, ScoreResult, compute_final_score, tier_for_score
from altcoin_trend.signals.explain import build_explain_text
from altcoin_trend.signals.ranking import rank_scores


def test_scoreinput_and_scoreresult_public_fields_match_plan():
    assert [field.name for field in fields(ScoreInput)] == [
        "trend_score",
        "volume_breakout_score",
        "relative_strength_score",
        "derivatives_score",
        "quality_score",
        "veto_reason_codes",
    ]
    assert [field.name for field in fields(ScoreResult)] == [
        "final_score",
        "tier",
        "primary_reason",
    ]


def test_compute_final_score_uses_mvp_weights_and_tier():
    result = compute_final_score(
        ScoreInput(
            trend_score=100,
            volume_breakout_score=80,
            relative_strength_score=60,
            derivatives_score=40,
            quality_score=100,
            veto_reason_codes=[],
        )
    )

    assert result.final_score == 78.0
    assert result.tier == "watchlist"
    assert result.primary_reason == ""


def test_compute_final_score_veto_forces_rejected_and_keeps_score():
    result = compute_final_score(
        ScoreInput(
            trend_score=100,
            volume_breakout_score=80,
            relative_strength_score=60,
            derivatives_score=40,
            quality_score=100,
            veto_reason_codes=["volume_breakout_low", "quality_low"],
        )
    )

    assert result.final_score == 78.0
    assert result.tier == "rejected"
    assert result.primary_reason == "volume_breakout_low"


def test_tier_boundaries_match_spec():
    assert tier_for_score(85) == "strong"
    assert tier_for_score(75) == "watchlist"
    assert tier_for_score(60) == "monitor"
    assert tier_for_score(59.9) == "rejected"


def test_rank_scores_orders_by_final_score_and_adds_scope_and_rank():
    rows = [
        {"symbol": "AAAUSDT", "final_score": 73.0},
        {"symbol": "BBBUSDT", "final_score": 91.5},
        {"symbol": "CCCUSDT", "final_score": 80.0},
    ]

    ranked = rank_scores(rows, rank_scope="all")

    assert [row["symbol"] for row in ranked] == ["BBBUSDT", "CCCUSDT", "AAAUSDT"]
    assert [row["rank"] for row in ranked] == [1, 2, 3]
    assert all(row["rank_scope"] == "all" for row in ranked)


def test_build_explain_text_includes_key_fields():
    text = build_explain_text(
        {
            "exchange": "binance",
            "symbol": "SOLUSDT",
            "final_score": 88.4,
            "tier": "strong",
            "trend_score": 92.0,
            "volume_breakout_score": 81.0,
            "relative_strength_score": 77.5,
            "derivatives_score": 64.0,
            "quality_score": 90.0,
            "veto_reason_codes": [],
        }
    )

    assert text.splitlines()[0] == "binance:SOLUSDT"
    assert "Score: 88.4" in text
    assert "Tier: strong" in text
    assert "Breakdown:" in text
    assert "Trend" in text
    assert "Veto: none" in text
