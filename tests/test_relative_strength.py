import pandas as pd

from altcoin_trend.features.relative_strength import build_relative_strength_features


def _rows(asset_id: int, symbol: str, closes: tuple[float, float, float]):
    points = (
        ("2026-01-01T00:00:00Z", closes[0]),
        ("2026-01-24T00:00:00Z", closes[1]),
        ("2026-01-31T00:00:00Z", closes[2]),
    )
    return [
        {
            "asset_id": asset_id,
            "exchange": "binance",
            "symbol": symbol,
            "base_asset": symbol.removesuffix("USDT"),
            "ts": ts,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1.0,
            "quote_volume": 1.0,
        }
        for ts, close in points
    ]


def test_relative_strength_features_compare_asset_returns_to_btc_and_eth():
    frame = pd.DataFrame(
        _rows(1, "BTCUSDT", (100.0, 100.0, 105.0))
        + _rows(2, "ETHUSDT", (100.0, 100.0, 110.0))
        + _rows(3, "SOLUSDT", (100.0, 100.0, 120.0))
    )

    features = build_relative_strength_features(frame)

    sol = features[3]
    assert sol.rs_btc_7d == 15.0
    assert sol.rs_eth_7d == 10.0
    assert sol.rs_btc_30d == 15.0
    assert sol.rs_eth_30d == 10.0
    assert sol.relative_strength_score > 80.0


def test_relative_strength_score_penalizes_underperformance_against_benchmarks():
    frame = pd.DataFrame(
        _rows(1, "BTCUSDT", (100.0, 100.0, 110.0))
        + _rows(2, "ETHUSDT", (100.0, 100.0, 120.0))
        + _rows(3, "LAGUSDT", (100.0, 100.0, 95.0))
    )

    features = build_relative_strength_features(frame)

    laggard = features[3]
    assert laggard.rs_btc_7d == -15.0
    assert laggard.rs_eth_7d == -25.0
    assert laggard.relative_strength_score < 30.0


def test_relative_strength_uses_cross_sectional_fallback_without_benchmarks():
    frame = pd.DataFrame(
        _rows(10, "LEADERUSDT", (100.0, 100.0, 130.0))
        + _rows(11, "MIDUSDT", (100.0, 100.0, 110.0))
        + _rows(12, "WEAKUSDT", (100.0, 100.0, 90.0))
    )

    features = build_relative_strength_features(frame)

    assert features[10].relative_strength_score > features[11].relative_strength_score
    assert features[11].relative_strength_score > features[12].relative_strength_score
    assert features[10].rs_btc_7d is None
    assert features[10].rs_eth_30d is None
