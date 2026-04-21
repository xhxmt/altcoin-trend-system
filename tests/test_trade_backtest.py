from datetime import datetime, timezone

import pandas as pd

from altcoin_trend.trade_backtest import (
    compute_forward_path_labels,
    evaluate_trade_candidate_bars,
    summarize_signal_v2_groups,
)


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


def test_compute_forward_path_labels_detects_target_before_drawdown():
    signal_close = 100.0
    future = pd.DataFrame(
        [
            {"ts": pd.Timestamp("2026-01-01T00:01:00Z"), "high": 104.0, "low": 99.0},
            {"ts": pd.Timestamp("2026-01-01T00:02:00Z"), "high": 111.0, "low": 98.0},
            {"ts": pd.Timestamp("2026-01-01T00:03:00Z"), "high": 112.0, "low": 90.0},
        ]
    )

    labels = compute_forward_path_labels(
        signal_ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        signal_close=signal_close,
        future_rows=future,
    )

    assert labels["mfe_1h_pct"] == 12.0
    assert labels["mae_1h_pct"] == 10.0
    assert labels["hit_10pct_before_drawdown_8pct"] is True
    assert labels["time_to_hit_10pct_minutes"] == 2.0


def test_compute_forward_path_labels_detects_drawdown_before_target():
    future = pd.DataFrame(
        [
            {"ts": pd.Timestamp("2026-01-01T00:01:00Z"), "high": 104.0, "low": 91.0},
            {"ts": pd.Timestamp("2026-01-01T00:02:00Z"), "high": 112.0, "low": 90.0},
        ]
    )
    labels = compute_forward_path_labels(
        signal_ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        signal_close=100.0,
        future_rows=future,
    )
    assert labels["hit_10pct_before_drawdown_8pct"] is False
    assert labels["time_to_hit_10pct_minutes"] is None


def test_compute_forward_path_labels_prefers_drawdown_when_same_row_hits_both():
    future = pd.DataFrame(
        [
            {"ts": pd.Timestamp("2026-01-01T00:01:00Z"), "high": 111.0, "low": 91.0},
        ]
    )
    labels = compute_forward_path_labels(
        signal_ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        signal_close=100.0,
        future_rows=future,
    )

    assert labels["hit_10pct_before_drawdown_8pct"] is False
    assert labels["time_to_hit_10pct_minutes"] is None


def test_compute_forward_path_labels_returns_empty_for_invalid_signal_close():
    future = pd.DataFrame(
        [
            {"ts": pd.Timestamp("2026-01-01T00:01:00Z"), "high": 111.0, "low": 90.0}
        ]
    )
    for close in (None, float("nan"), pd.NA, 0.0, -1.0):
        labels = compute_forward_path_labels(
            signal_ts=pd.Timestamp("2026-01-01T00:00:00Z"),
            signal_close=close,
            future_rows=future,
        )
        assert labels["mfe_1h_pct"] == 0.0
        assert labels["mae_1h_pct"] == 0.0
        assert labels["hit_10pct_before_drawdown_8pct"] is False
        assert labels["time_to_hit_10pct_minutes"] is None


def test_compute_forward_path_labels_handles_empty_future_rows():
    labels = compute_forward_path_labels(
        signal_ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        signal_close=100.0,
        future_rows=pd.DataFrame(),
    )

    assert labels["mfe_1h_pct"] == 0.0
    assert labels["mfe_4h_pct"] == 0.0
    assert labels["mfe_24h_pct"] == 0.0
    assert labels["mae_1h_pct"] == 0.0
    assert labels["mae_4h_pct"] == 0.0
    assert labels["mae_24h_pct"] == 0.0
    assert labels["hit_5pct_before_drawdown_5pct"] is False
    assert labels["hit_10pct_before_drawdown_8pct"] is False
    assert labels["time_to_hit_5pct_minutes"] is None
    assert labels["time_to_hit_10pct_minutes"] is None


def test_summarize_signal_v2_groups_reports_by_grade():
    signals = pd.DataFrame(
        [
            {
                "continuation_grade": "A",
                "ignition_grade": None,
                "mfe_1h_pct": 12.0,
                "mfe_4h_pct": 15.0,
                "mfe_24h_pct": 18.0,
                "mae_1h_pct": 3.0,
                "mae_4h_pct": 4.0,
                "mae_24h_pct": 5.0,
                "hit_10pct_before_drawdown_8pct": True,
                "time_to_hit_10pct_minutes": 30.0,
                "cross_exchange_confirmed": True,
                "chase_risk_score": 20.0,
            },
            {
                "continuation_grade": None,
                "ignition_grade": "B",
                "mfe_1h_pct": 4.0,
                "mfe_4h_pct": 6.0,
                "mfe_24h_pct": 7.0,
                "mae_1h_pct": 9.0,
                "mae_4h_pct": 10.0,
                "mae_24h_pct": 11.0,
                "hit_10pct_before_drawdown_8pct": False,
                "time_to_hit_10pct_minutes": None,
                "cross_exchange_confirmed": False,
                "chase_risk_score": 80.0,
            },
        ]
    )

    summary = summarize_signal_v2_groups(signals)

    assert summary["continuation_A"]["signal_count"] == 1
    assert summary["continuation_A"]["hit_5pct_rate"] == 100.0
    assert summary["continuation_A"]["hit_10pct_rate"] == 100.0
    assert summary["continuation_A"]["hit_10pct_before_drawdown_8pct_rate"] == 100.0
    assert summary["ignition_B"]["signal_count"] == 1
    assert summary["ignition_B"]["avg_mae_1h_pct"] == 9.0
    assert summary["ignition_B"]["avg_mfe_4h_pct"] == 6.0
    assert summary["ignition_B"]["median_time_to_hit_10pct_minutes"] == 0.0


def test_summarize_signal_v2_groups_requires_explicit_drawdown_column_for_drawdown_rate():
    signals = pd.DataFrame(
        [
            {
                "continuation_grade": "A",
                "ignition_grade": None,
                "mfe_1h_pct": 12.0,
                "mfe_4h_pct": 15.0,
                "mfe_24h_pct": 18.0,
                "mae_1h_pct": 3.0,
                "mae_4h_pct": 4.0,
                "mae_24h_pct": 5.0,
                "hit_10pct_rate": True,
                "time_to_hit_10pct_minutes": 30.0,
                "cross_exchange_confirmed": True,
                "chase_risk_score": 20.0,
            },
            {
                "continuation_grade": "A",
                "ignition_grade": None,
                "mfe_1h_pct": 11.0,
                "mfe_4h_pct": 14.0,
                "mfe_24h_pct": 17.0,
                "mae_1h_pct": 2.0,
                "mae_4h_pct": 3.0,
                "mae_24h_pct": 4.0,
                "hit_10pct_rate": False,
                "time_to_hit_10pct_minutes": None,
                "cross_exchange_confirmed": False,
                "chase_risk_score": 80.0,
            },
        ]
    )

    summary = summarize_signal_v2_groups(signals)

    assert summary["continuation_A"]["hit_10pct_rate"] == 50.0
    assert summary["continuation_A"]["hit_10pct_before_drawdown_8pct_rate"] == 0.0


def test_summarize_signal_v2_groups_uses_plain_mfe_rate_for_hit_10pct_rate():
    signals = pd.DataFrame(
        [
            {
                "continuation_grade": "A",
                "ignition_grade": None,
                "mfe_1h_pct": 10.0,
                "mfe_4h_pct": 12.0,
                "mfe_24h_pct": 14.0,
                "mae_1h_pct": 1.0,
                "mae_4h_pct": 2.0,
                "mae_24h_pct": 3.0,
                "hit_10pct_before_drawdown_8pct": False,
                "time_to_hit_10pct_minutes": None,
                "cross_exchange_confirmed": True,
                "chase_risk_score": 20.0,
            }
        ]
    )

    summary = summarize_signal_v2_groups(signals)

    assert summary["continuation_A"]["hit_10pct_rate"] == 100.0
    assert summary["continuation_A"]["hit_10pct_before_drawdown_8pct_rate"] == 0.0


def test_summarize_signal_v2_groups_handles_empty_frame():
    summary = summarize_signal_v2_groups(pd.DataFrame())
    assert summary["continuation_A"]["signal_count"] == 0
    assert summary["ignition_EXTREME"]["avg_mfe_1h_pct"] == 0.0
    assert summary["single_exchange_triggered"]["signal_count"] == 0


def test_summarize_signal_v2_groups_reports_cross_exchange_and_chase_risk_groups():
    signals = pd.DataFrame(
        [
            {
                "continuation_grade": "A",
                "ignition_grade": None,
                "mfe_1h_pct": 12.0,
                "mfe_4h_pct": 15.0,
                "mfe_24h_pct": 20.0,
                "mae_1h_pct": 2.0,
                "mae_4h_pct": 3.0,
                "mae_24h_pct": 4.0,
                "hit_10pct_before_drawdown_8pct": True,
                "time_to_hit_10pct_minutes": 30.0,
                "cross_exchange_confirmed": True,
                "chase_risk_score": 20.0,
            },
            {
                "continuation_grade": None,
                "ignition_grade": "EXTREME",
                "mfe_1h_pct": 8.0,
                "mfe_4h_pct": 9.0,
                "mfe_24h_pct": 10.0,
                "mae_1h_pct": 6.0,
                "mae_4h_pct": 7.0,
                "mae_24h_pct": 8.0,
                "hit_10pct_before_drawdown_8pct": False,
                "time_to_hit_10pct_minutes": None,
                "cross_exchange_confirmed": False,
                "chase_risk_score": 80.0,
            },
        ]
    )

    summary = summarize_signal_v2_groups(signals)

    assert summary["cross_exchange_confirmed"]["signal_count"] == 1
    assert summary["single_exchange_triggered"]["signal_count"] == 1
    assert summary["high_chase_risk"]["signal_count"] == 1
    assert summary["low_or_medium_chase_risk"]["signal_count"] == 1
    assert summary["cross_exchange_confirmed"]["median_time_to_hit_10pct_minutes"] == 30.0
