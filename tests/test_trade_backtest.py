from datetime import datetime, timezone

import pandas as pd

from altcoin_trend.trade_backtest import evaluate_trade_candidate_bars


def _hourly_rows(asset_id: int, symbol: str, multiplier: float):
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    for hour in range(24 * 31 + 2):
        close = 100.0 + hour * 0.01 * multiplier
        if symbol == "FASTUSDT" and hour >= 24 * 30 - 24:
            close += (hour - (24 * 30 - 24)) * 0.7
        if symbol == "FASTUSDT" and hour == 24 * 31 - 1:
            close *= 1.13
        quote_volume = 1000.0
        if symbol == "FASTUSDT" and hour == 24 * 31 - 1:
            quote_volume = 10_000.0
        future_high = close * 1.01
        if symbol == "FASTUSDT" and hour == 24 * 31:
            previous_close = 100.0 + (hour - 1) * 0.01 * multiplier
            previous_close += (hour - 1 - (24 * 30 - 24)) * 0.7
            previous_close *= 1.13
            future_high = previous_close * 1.12
        rows.append(
            {
                "asset_id": asset_id,
                "exchange": "binance",
                "symbol": symbol,
                "ts": start + pd.Timedelta(hours=hour),
                "open": close,
                "high": future_high,
                "low": close * 0.99,
                "close": close,
                "volume": quote_volume,
                "quote_volume": quote_volume,
            }
        )
    return rows


def test_evaluate_trade_candidate_bars_scores_forward_1h_high_hits():
    frame = pd.DataFrame(
        _hourly_rows(1, "BTCUSDT", 1.0)
        + _hourly_rows(2, "ETHUSDT", 1.0)
        + _hourly_rows(3, "FASTUSDT", 1.0)
        + _hourly_rows(4, "SLOWUSDT", 0.5)
    )

    summary = evaluate_trade_candidate_bars(
        frame,
        start=datetime(2026, 1, 31, tzinfo=timezone.utc),
        end=datetime(2026, 2, 2, tzinfo=timezone.utc),
        target_return=0.10,
        limit=5,
    )

    assert summary.signal_count >= 1
    assert summary.hit_count >= 1
    assert summary.precision > 0
    assert summary.top_signals[0]["symbol"] == "FASTUSDT"
