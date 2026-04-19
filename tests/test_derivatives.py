import pandas as pd

from altcoin_trend.features.derivatives import compute_derivatives_features


def test_derivatives_features_return_neutral_without_derivatives_data():
    frame = pd.DataFrame(
        [
            {"ts": "2026-01-01T00:00:00Z", "close": 100.0, "quote_volume": 1000.0},
            {"ts": "2026-01-01T01:00:00Z", "close": 105.0, "quote_volume": 1000.0},
        ]
    )

    features = compute_derivatives_features(frame)

    assert features.derivatives_score == 50.0
    assert features.oi_delta_1h is None
    assert features.funding_zscore is None


def test_derivatives_features_reward_price_up_with_open_interest_up():
    frame = pd.DataFrame(
        [
            {
                "ts": "2026-01-01T00:00:00Z",
                "close": 100.0,
                "open_interest": 1000.0,
                "funding_rate": 0.0001,
                "quote_volume": 1000.0,
                "taker_buy_quote": 520.0,
            },
            {
                "ts": "2026-01-01T01:00:00Z",
                "close": 105.0,
                "open_interest": 1100.0,
                "funding_rate": 0.0001,
                "quote_volume": 1000.0,
                "taker_buy_quote": 560.0,
            },
            {
                "ts": "2026-01-01T04:00:00Z",
                "close": 112.0,
                "open_interest": 1250.0,
                "funding_rate": 0.00012,
                "quote_volume": 1000.0,
                "taker_buy_quote": 570.0,
            },
        ]
    )

    features = compute_derivatives_features(frame)

    assert features.oi_delta_1h > 0
    assert features.oi_delta_4h > 0
    assert features.taker_buy_sell_ratio > 1.0
    assert features.derivatives_score > 60.0


def test_derivatives_features_penalize_price_up_with_oi_down_and_hot_funding():
    frame = pd.DataFrame(
        [
            {
                "ts": "2026-01-01T00:00:00Z",
                "close": 100.0,
                "open_interest": 1000.0,
                "funding_rate": 0.0001,
                "quote_volume": 1000.0,
                "taker_buy_quote": 900.0,
            },
            {
                "ts": "2026-01-01T01:00:00Z",
                "close": 105.0,
                "open_interest": 900.0,
                "funding_rate": 0.0002,
                "quote_volume": 1000.0,
                "taker_buy_quote": 920.0,
            },
            {
                "ts": "2026-01-01T04:00:00Z",
                "close": 112.0,
                "open_interest": 800.0,
                "funding_rate": 0.0020,
                "quote_volume": 1000.0,
                "taker_buy_quote": 930.0,
            },
        ]
    )

    features = compute_derivatives_features(frame)

    assert features.oi_delta_1h < 0
    assert features.oi_delta_4h < 0
    assert features.funding_zscore > 1.0
    assert features.derivatives_score < 45.0
