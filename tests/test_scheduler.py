from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pandas as pd
import pytest

from altcoin_trend.scheduler import RunOnceResult, build_snapshot_rows, process_alerts, run_once_pipeline


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


def _relative_strength_rows(asset_id: int, symbol: str, closes: tuple[float, float, float]):
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
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 10.0,
            "quote_volume": 1000.0,
        }
        for ts, close in points
    ]


def test_build_snapshot_rows_uses_data_driven_relative_strength():
    snapshot_ts = datetime(2026, 1, 31, tzinfo=timezone.utc)
    market_rows = pd.DataFrame(
        _relative_strength_rows(1, "BTCUSDT", (100.0, 100.0, 105.0))
        + _relative_strength_rows(2, "ETHUSDT", (100.0, 100.0, 110.0))
        + _relative_strength_rows(3, "SOLUSDT", (100.0, 100.0, 120.0))
        + _relative_strength_rows(4, "LAGUSDT", (100.0, 100.0, 95.0))
    )

    feature_rows, rank_rows = build_snapshot_rows(market_rows, snapshot_ts)
    by_symbol = {row["symbol"]: row for row in feature_rows}

    assert by_symbol["SOLUSDT"]["rs_btc_7d"] == 15.0
    assert by_symbol["SOLUSDT"]["rs_eth_7d"] == 10.0
    assert by_symbol["SOLUSDT"]["rs_btc_30d"] == 15.0
    assert by_symbol["SOLUSDT"]["rs_eth_30d"] == 10.0
    assert by_symbol["SOLUSDT"]["relative_strength_score"] > 80.0
    assert by_symbol["LAGUSDT"]["relative_strength_score"] < 30.0
    assert by_symbol["SOLUSDT"]["final_score"] > by_symbol["LAGUSDT"]["final_score"]
    assert rank_rows[0]["symbol"] == "SOLUSDT"


def test_build_snapshot_rows_uses_data_driven_derivatives_score():
    snapshot_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    market_rows = pd.DataFrame(
        [
            {
                "asset_id": 50,
                "exchange": "binance",
                "symbol": "SOLUSDT",
                "base_asset": "SOL",
                "ts": pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=hour),
                "open": 100.0 + hour,
                "high": 101.0 + hour,
                "low": 99.0 + hour,
                "close": 100.0 + hour * 3,
                "volume": 10.0,
                "quote_volume": 1000.0,
                "taker_buy_quote": 560.0,
                "open_interest": 1000.0 + hour * 100.0,
                "funding_rate": 0.0001,
            }
            for hour in range(5)
        ]
    )

    feature_rows, _ = build_snapshot_rows(market_rows, snapshot_ts)
    row = feature_rows[0]

    assert row["oi_delta_1h"] > 0
    assert row["oi_delta_4h"] > 0
    assert row["taker_buy_sell_ratio"] > 1.0
    assert row["derivatives_score"] > 50.0


def test_build_snapshot_rows_penalizes_extreme_extension_above_4h_ema():
    snapshot_ts = datetime(2026, 2, 1, tzinfo=timezone.utc)
    rows = []
    for minute in range(31 * 24 * 60):
        ts = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=minute)
        steady_close = 100.0 + 0.01 * minute
        extended_close = 100.0 + 0.01 * minute
        if minute > 31 * 24 * 60 - 240:
            extended_close += 90.0
        for asset_id, symbol, close in (
            (20, "STEADYUSDT", steady_close),
            (21, "EXTENDEDUSDT", extended_close),
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

    feature_rows, _ = build_snapshot_rows(pd.DataFrame(rows), snapshot_ts)
    by_symbol = {row["symbol"]: row for row in feature_rows}

    assert by_symbol["STEADYUSDT"]["trend_score"] > by_symbol["EXTENDEDUSDT"]["trend_score"]


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


def test_load_market_rows_queries_recent_31_day_window():
    captured = {}

    class Result:
        def mappings(self):
            return self

        def all(self):
            return []

    class Connection:
        def execute(self, statement, params):
            captured["sql"] = str(statement)
            captured["params"] = params
            return Result()

    class Begin:
        def __enter__(self):
            return Connection()

        def __exit__(self, exc_type, exc, tb):
            return False

    class Engine:
        def begin(self):
            return Begin()

    from altcoin_trend.scheduler import _load_market_rows

    frame = _load_market_rows(Engine(), lookback_days=31)

    assert frame.empty
    assert "MAX(ts)" in captured["sql"]
    assert "make_interval(days => :lookback_days)" in captured["sql"]
    assert captured["params"] == {"lookback_days": 31}


def test_load_market_rows_selects_derivatives_columns():
    captured = {}

    class Result:
        def mappings(self):
            return self

        def all(self):
            return []

    class Connection:
        def execute(self, statement, params):
            captured["sql"] = str(statement)
            return Result()

    class Begin:
        def __enter__(self):
            return Connection()

        def __exit__(self, exc_type, exc, tb):
            return False

    class Engine:
        def begin(self):
            return Begin()

    from altcoin_trend.scheduler import _load_market_rows

    _load_market_rows(Engine())

    assert "m.open_interest" in captured["sql"]
    assert "m.funding_rate" in captured["sql"]
    assert "m.long_short_ratio" in captured["sql"]
    assert "m.buy_sell_ratio" in captured["sql"]


def test_load_rank_rows_selects_alert_gate_fields_from_feature_snapshot():
    captured = {}

    class Result:
        def mappings(self):
            return self

        def all(self):
            return []

    class Connection:
        def execute(self, statement, params):
            captured["sql"] = str(statement)
            captured["params"] = params
            return Result()

    class Begin:
        def __enter__(self):
            return Connection()

        def __exit__(self, exc_type, exc, tb):
            return False

    class Engine:
        def begin(self):
            return Begin()

    from altcoin_trend.scheduler import load_rank_rows

    load_rank_rows(Engine(), rank_scope="all", limit=30)

    assert "JOIN alt_signal.feature_snapshot AS fs" in captured["sql"]
    assert "fs.trend_score" in captured["sql"]
    assert "fs.relative_strength_score" in captured["sql"]
    assert "fs.derivatives_score" in captured["sql"]
    assert "fs.quality_score" in captured["sql"]
    assert "fs.volume_breakout_score" in captured["sql"]
    assert "fs.veto_reason_codes" in captured["sql"]
    assert captured["params"] == {"rank_scope": "all", "limit": 30}


def test_process_alerts_inserts_and_sends_new_alert(monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    inserted = []

    monkeypatch.setattr(
        "altcoin_trend.scheduler.load_rank_rows",
        lambda engine, rank_scope, limit: [
            {
                "asset_id": 17,
                "exchange": "binance",
                "symbol": "SOLUSDT",
                "tier": "strong",
                "final_score": 88.4,
                "trend_score": 90.0,
                "volume_breakout_score": 80.0,
                "relative_strength_score": 75.0,
                "derivatives_score": 60.0,
                "quality_score": 100.0,
                "veto_reason_codes": [],
            }
        ],
    )
    monkeypatch.setattr("altcoin_trend.scheduler._load_recent_alert_events", lambda engine, since: [])

    def fake_insert_rows(engine, table_name, rows):
        inserted.append((table_name, list(rows)))
        return len(inserted[-1][1])

    monkeypatch.setattr("altcoin_trend.scheduler.insert_rows", fake_insert_rows)

    class Telegram:
        messages = []

        def send_message(self, text):
            self.messages.append(text)

            class Result:
                ok = True
                error = ""

            return Result()

    telegram = Telegram()

    inserted_count, sent_count = process_alerts(
        engine=object(),
        now=now,
        cooldown_seconds=3600,
        telegram_client=telegram,
    )

    assert inserted_count == 1
    assert sent_count == 1
    assert inserted[0][0] == "alt_signal.alert_events"
    assert inserted[0][1][0]["delivery_status"] == "sent"
    assert "[STRONG] SOLUSDT Binance" in telegram.messages[0]
