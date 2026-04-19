from datetime import datetime, timedelta, timezone

import pytest

from altcoin_trend.backtest import BacktestSummary, HorizonStats, parse_horizons, run_signal_backtest, summarize_backtest


def test_parse_horizons_parses_hours_and_days():
    horizons = parse_horizons("1h,4h,24h,1d")

    assert horizons == (
        ("1h", timedelta(hours=1)),
        ("4h", timedelta(hours=4)),
        ("24h", timedelta(hours=24)),
        ("1d", timedelta(days=1)),
    )


@pytest.mark.parametrize("value", ["", "abc", "1w", "1.5h", "0h", "1", "h"])
def test_parse_horizons_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_horizons(value)


def test_summarize_backtest_computes_counts_and_horizon_stats():
    signals = [
        {"exchange": "binance", "symbol": "SOLUSDT", "final_score": 90.0, "tier": "strong"},
        {"exchange": "bybit", "symbol": "ETHUSDT", "final_score": 80.0, "tier": "watchlist"},
    ]
    returns_by_horizon = {
        "1h": [0.10, -0.05],
        "4h": [0.20, 0.30],
    }

    summary = summarize_backtest(signals, returns_by_horizon, limit=1)

    assert summary == BacktestSummary(
        signal_count=2,
        average_score=85.0,
        tier_counts={"strong": 1, "watchlist": 1},
        exchange_counts={"binance": 1, "bybit": 1},
        horizon_stats={
            "1h": HorizonStats(avg_return=0.025, win_rate=50.0),
            "4h": HorizonStats(avg_return=0.25, win_rate=100.0),
        },
        top_signals=[{"exchange": "binance", "symbol": "SOLUSDT", "final_score": 90.0, "tier": "strong"}],
    )


def test_run_signal_backtest_filters_and_uses_next_market_close(monkeypatch):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=2)
    horizons = (("1h", timedelta(hours=1)), ("4h", timedelta(hours=4)), ("1d", timedelta(days=1)))
    snapshots = [
        {
            "ts": start,
            "asset_id": 1,
            "exchange": "binance",
            "symbol": "SOLUSDT",
            "close": 100.0,
            "final_score": 90.0,
            "tier": "strong",
            "trend_score": 90.0,
            "volume_breakout_score": 90.0,
            "relative_strength_score": 90.0,
            "derivatives_score": 90.0,
            "quality_score": 90.0,
            "veto_reason_codes": [],
        },
        {
            "ts": start + timedelta(hours=1),
            "asset_id": 2,
            "exchange": "bybit",
            "symbol": "ETHUSDT",
            "close": 200.0,
            "final_score": 80.0,
            "tier": "watchlist",
            "trend_score": 50.0,
            "volume_breakout_score": 50.0,
            "relative_strength_score": 50.0,
            "derivatives_score": 50.0,
            "quality_score": 50.0,
            "veto_reason_codes": [],
        },
        {
            "ts": start + timedelta(hours=2),
            "asset_id": 3,
            "exchange": "binance",
            "symbol": "XRPUSDT",
            "close": 300.0,
            "final_score": 95.0,
            "tier": "strong",
            "trend_score": 95.0,
            "volume_breakout_score": 95.0,
            "relative_strength_score": 95.0,
            "derivatives_score": 95.0,
            "quality_score": 95.0,
            "veto_reason_codes": [],
        },
    ]
    market_rows = [
        {"asset_id": 1, "ts": start + timedelta(hours=1), "close": 110.0},
        {"asset_id": 1, "ts": start + timedelta(hours=4), "close": 95.0},
        {"asset_id": 1, "ts": start + timedelta(days=1), "close": 120.0},
        {"asset_id": 2, "ts": start + timedelta(hours=2), "close": 210.0},
        {"asset_id": 2, "ts": start + timedelta(hours=5), "close": 220.0},
        {"asset_id": 2, "ts": start + timedelta(days=1, hours=1), "close": 240.0},
        {"asset_id": 3, "ts": start + timedelta(hours=3), "close": 330.0},
        {"asset_id": 3, "ts": start + timedelta(hours=6), "close": 360.0},
        {"asset_id": 3, "ts": start + timedelta(days=1, hours=2), "close": 390.0},
    ]

    class Result:
        def __init__(self, rows):
            self._rows = rows

        def mappings(self):
            return self

        def all(self):
            return list(self._rows)

        def first(self):
            return self._rows[0] if self._rows else None

    class Connection:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params=None):
            sql = str(statement)
            self.calls.append((sql, params))
            if "alt_signal.feature_snapshot" in sql:
                return Result(snapshots)
            if "alt_core.market_1m" in sql:
                matching = [
                    row
                    for row in market_rows
                    if row["asset_id"] == params["asset_id"] and row["ts"] >= params["target_ts"]
                ]
                return Result(matching[:1])
            raise AssertionError(sql)

    class Begin:
        def __enter__(self):
            return Connection()

        def __exit__(self, exc_type, exc, tb):
            return False

    class Engine:
        def begin(self):
            return Begin()

    monkeypatch.setattr("altcoin_trend.backtest.is_high_value_signal", lambda row: row["asset_id"] != 2)

    summary = run_signal_backtest(
        Engine(),
        start=start,
        end=end,
        min_score=85.0,
        horizons=horizons,
        high_value_only=True,
        limit=2,
    )

    assert summary.signal_count == 2
    assert summary.average_score == 92.5
    assert summary.tier_counts == {"strong": 2}
    assert summary.exchange_counts == {"binance": 2}
    assert summary.horizon_stats["1h"] == HorizonStats(avg_return=0.1, win_rate=100.0)
    assert summary.horizon_stats["4h"] == HorizonStats(avg_return=0.075, win_rate=50.0)
    assert summary.horizon_stats["1d"] == HorizonStats(avg_return=0.25, win_rate=100.0)
    assert [row["symbol"] for row in summary.top_signals] == ["XRPUSDT", "SOLUSDT"]
