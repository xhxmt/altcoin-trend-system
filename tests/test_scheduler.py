from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pandas as pd
import pytest

from altcoin_trend.scheduler import RunOnceResult, build_snapshot_rows, run_once_pipeline


def test_run_once_pipeline_defaults_to_degraded_when_no_step():
    result = run_once_pipeline()

    assert result.status == "degraded"
    assert result.message == "no pipeline step configured"
    assert result.started_at.tzinfo == timezone.utc


def test_run_once_pipeline_returns_healthy_step_message():
    result = run_once_pipeline(lambda: "pipeline complete")

    assert result.status == "healthy"
    assert result.message == "pipeline complete"


def test_run_once_result_is_frozen():
    result = RunOnceResult(datetime.now(timezone.utc), "healthy", "ok")

    with pytest.raises(FrozenInstanceError):
        result.status = "degraded"  # type: ignore[misc]


def test_build_snapshot_rows_scores_and_ranks_latest_market_data():
    snapshot_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    market_rows = pd.DataFrame(
        [
            {
                "asset_id": 1,
                "exchange": "binance",
                "symbol": "SOLUSDT",
                "base_asset": "SOL",
                "ts": "2026-01-01T00:00:00Z",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 10.0,
                "quote_volume": 1000.0,
            },
            {
                "asset_id": 1,
                "exchange": "binance",
                "symbol": "SOLUSDT",
                "base_asset": "SOL",
                "ts": "2026-01-01T00:01:00Z",
                "open": 100.0,
                "high": 103.0,
                "low": 100.0,
                "close": 102.0,
                "volume": 20.0,
                "quote_volume": 3000.0,
            },
        ]
    )

    feature_rows, rank_rows = build_snapshot_rows(market_rows, snapshot_ts)

    assert len(feature_rows) == 1
    assert feature_rows[0]["symbol"] == "SOLUSDT"
    assert feature_rows[0]["ts"] == snapshot_ts
    assert feature_rows[0]["final_score"] > 0
    assert rank_rows[0]["rank_scope"] == "all"
    assert rank_rows[0]["rank"] == 1


def test_build_snapshot_rows_includes_higher_timeframe_features():
    snapshot_ts = datetime(2026, 1, 2, tzinfo=timezone.utc)
    market_rows = pd.DataFrame(
        [
            {
                "asset_id": 1,
                "exchange": "binance",
                "symbol": "SOLUSDT",
                "base_asset": "SOL",
                "ts": pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=minute),
                "open": 100.0 + minute * 0.01,
                "high": 101.0 + minute * 0.01,
                "low": 99.0 + minute * 0.01,
                "close": 100.5 + minute * 0.01,
                "volume": 10.0 + minute % 5,
                "quote_volume": 1000.0 + minute * 2,
            }
            for minute in range(24 * 60)
        ]
    )

    feature_rows, _ = build_snapshot_rows(market_rows, snapshot_ts)

    row = feature_rows[0]
    assert row["ema20_4h"] is not None
    assert row["ema60_4h"] is not None
    assert row["ema20_1d"] is not None
    assert row["ema60_1d"] is not None
    assert row["atr14_4h"] > 0
    assert row["adx14_4h"] >= 0
    assert row["volume_ratio_4h"] > 0
    assert isinstance(row["breakout_20d"], bool)


def test_build_snapshot_rows_scores_aligned_4h_trend_above_fading_move():
    snapshot_ts = datetime(2026, 1, 2, tzinfo=timezone.utc)
    rows = []
    for minute in range(24 * 60):
        ts = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=minute)
        strong_close = 100.0 + 20.0 * minute / (24 * 60 - 1)
        if minute < 12 * 60:
            fading_close = 100.0 + 40.0 * minute / (12 * 60)
        else:
            fading_close = 140.0 - 20.0 * (minute - 12 * 60) / (12 * 60 - 1)
        for asset_id, symbol, close in (
            (1, "STRONGUSDT", strong_close),
            (2, "FADINGUSDT", fading_close),
        ):
            rows.append(
                {
                    "asset_id": asset_id,
                    "exchange": "binance",
                    "symbol": symbol,
                    "base_asset": symbol.removesuffix("USDT"),
                    "ts": ts,
                    "open": close,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 10.0,
                    "quote_volume": 1000.0,
                }
            )
    market_rows = pd.DataFrame(rows)

    feature_rows, rank_rows = build_snapshot_rows(market_rows, snapshot_ts)
    scores = {row["symbol"]: row["final_score"] for row in feature_rows}

    assert scores["STRONGUSDT"] > scores["FADINGUSDT"]
    assert rank_rows[0]["symbol"] == "STRONGUSDT"


def test_run_once_pipeline_writes_snapshots_with_engine(monkeypatch):
    calls = []

    monkeypatch.setattr("altcoin_trend.scheduler.write_run_once_snapshots", lambda engine, snapshot_ts: (2, 4))

    class Engine:
        pass

    result = run_once_pipeline(engine=Engine(), now=datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert result.status == "healthy"
    assert result.message == "features_written=2 ranks_written=4"


def test_run_once_pipeline_reports_degraded_when_no_market_rows(monkeypatch):
    monkeypatch.setattr("altcoin_trend.scheduler.write_run_once_snapshots", lambda engine, snapshot_ts: (0, 0))

    class Engine:
        pass

    result = run_once_pipeline(engine=Engine(), now=datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert result.status == "degraded"
    assert result.message == "no market rows available"
