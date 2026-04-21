from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pandas as pd
import pytest
from psycopg.types.json import Jsonb

from altcoin_trend.scheduler import RunOnceResult, build_snapshot_rows, process_alerts, run_once_pipeline
from altcoin_trend.signals.v2 import compute_volume_impulse_score, evaluate_signal_v2


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


def test_build_snapshot_rows_marks_research_trade_candidate():
    snapshot_ts = datetime(2026, 2, 1, tzinfo=timezone.utc)
    rows = []
    for hour in range(24 * 31):
        ts = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=hour)
        btc_close = 100.0 + hour * 0.01
        eth_close = 100.0 + hour * 0.01
        strong_close = 100.0 + hour * 0.01
        weak_close = 100.0 + hour * 0.005
        if hour >= 24 * 30 - 24:
            strong_close += (hour - (24 * 30 - 24)) * 0.7
        if hour == 24 * 31 - 1:
            strong_close *= 1.13
        for asset_id, symbol, close, quote_volume in (
            (1, "BTCUSDT", btc_close, 1000.0),
            (2, "ETHUSDT", eth_close, 1000.0),
            (3, "FASTUSDT", strong_close, 1000.0),
            (4, "SLOWUSDT", weak_close, 1000.0),
        ):
            volume = quote_volume
            if symbol == "FASTUSDT" and hour == 24 * 31 - 1:
                volume = 10_000.0
            rows.append(
                {
                    "asset_id": asset_id,
                    "exchange": "binance",
                    "symbol": symbol,
                    "base_asset": symbol.removesuffix("USDT"),
                    "ts": ts,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": volume,
                    "quote_volume": volume,
                }
            )

    feature_rows, rank_rows = build_snapshot_rows(pd.DataFrame(rows), snapshot_ts)
    by_symbol = {row["symbol"]: row for row in feature_rows}

    assert by_symbol["FASTUSDT"]["return_1h_pct"] >= 6.0
    assert by_symbol["FASTUSDT"]["return_4h_pct"] >= 10.0
    assert by_symbol["FASTUSDT"]["return_24h_pct"] >= 12.0
    assert by_symbol["FASTUSDT"]["volume_ratio_24h"] >= 5.0
    assert by_symbol["FASTUSDT"]["return_24h_percentile"] >= 0.94
    assert by_symbol["FASTUSDT"]["return_7d_percentile"] >= 0.84
    assert by_symbol["FASTUSDT"]["trade_candidate"] is True
    assert by_symbol["SLOWUSDT"]["trade_candidate"] is False
    assert next(row for row in rank_rows if row["symbol"] == "FASTUSDT")["payload"]["trade_candidate"] is True


def test_build_snapshot_rows_marks_ignition_candidate_and_overrides_tier():
    snapshot_ts = datetime(2026, 2, 1, tzinfo=timezone.utc)
    rows = []
    for hour in range(24 * 31):
        ts = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=hour)
        btc_close = 100.0 + hour * 0.01
        eth_close = 100.0 + hour * 0.01
        ignition_close = 100.0 + hour * 0.01
        if hour == 24 * 31 - 25:
            ignition_close = 100.0
        elif hour > 24 * 31 - 25:
            ignition_close = 125.0
        if hour == 24 * 31 - 1:
            ignition_close = 170.0
        for asset_id, symbol, close, quote_volume in (
            (1, "BTCUSDT", btc_close, 1000.0),
            (2, "ETHUSDT", eth_close, 1000.0),
            (3, "RAVEUSDT", ignition_close, 1000.0),
        ):
            if symbol == "RAVEUSDT" and hour == 24 * 31 - 1:
                quote_volume = 2000.0
            rows.append(
                {
                    "asset_id": asset_id,
                    "exchange": "binance",
                    "symbol": symbol,
                    "base_asset": symbol.removesuffix("USDT"),
                    "ts": ts,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": quote_volume,
                    "quote_volume": quote_volume,
                }
            )

    feature_rows, rank_rows = build_snapshot_rows(pd.DataFrame(rows), snapshot_ts)
    row = next(row for row in feature_rows if row["symbol"] == "RAVEUSDT")
    rank_row = next(row for row in rank_rows if row["symbol"] == "RAVEUSDT" and row["rank_scope"] == "all")

    assert row["return_1h_pct"] >= 15.0
    assert row["return_24h_pct"] >= 60.0
    assert row["ignition_candidate"] is True
    assert row["continuation_candidate"] is False
    assert row["trade_candidate"] is False
    assert row["tier"] == "strong"
    assert rank_row["tier"] == "strong"
    assert rank_row["payload"]["ignition_candidate"] is True
    assert rank_row["payload"]["continuation_candidate"] is False


def test_build_snapshot_rows_populates_signal_v2_fields_and_keeps_trade_candidate_compatible():
    snapshot_ts = datetime(2026, 2, 1, tzinfo=timezone.utc)
    rows = []
    for hour in range(24 * 31):
        ts = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=hour)
        btc_close = 100.0 + hour * 0.01
        eth_close = 100.0 + hour * 0.01
        fast_close = 100.0 + hour * 0.01
        rave_close = 100.0 + hour * 0.01
        if hour >= 24 * 30 - 24:
            fast_close += (hour - (24 * 30 - 24)) * 0.7
        if hour == 24 * 31 - 1:
            fast_close *= 1.13
            rave_close = 220.0
        elif hour > 24 * 31 - 25:
            rave_close = 125.0

        for asset_id, symbol, close, quote_volume in (
            (1, "BTCUSDT", btc_close, 1000.0),
            (2, "ETHUSDT", eth_close, 1000.0),
            (3, "FASTUSDT", fast_close, 10000.0 if hour == 24 * 31 - 1 else 1000.0),
            (4, "RAVEUSDT", rave_close, 2500.0 if hour == 24 * 31 - 1 else 1000.0),
        ):
            rows.append(
                {
                    "asset_id": asset_id,
                    "exchange": "binance",
                    "symbol": symbol,
                    "base_asset": symbol.removesuffix("USDT"),
                    "ts": ts,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": quote_volume,
                    "quote_volume": quote_volume,
                    "open_interest": 1000.0 + hour,
                    "funding_rate": 0.0001,
                    "taker_buy_quote": quote_volume * 0.56,
                }
            )

    feature_rows, rank_rows = build_snapshot_rows(pd.DataFrame(rows), snapshot_ts)
    by_symbol = {row["symbol"]: row for row in feature_rows}

    assert by_symbol["FASTUSDT"]["return_24h_rank"] is not None
    assert by_symbol["FASTUSDT"]["return_7d_rank"] is not None
    assert by_symbol["FASTUSDT"]["volume_ratio_1h"] is not None
    assert by_symbol["FASTUSDT"]["volume_impulse_score"] >= 0.0
    assert by_symbol["FASTUSDT"]["continuation_grade"] in {"A", "B"}
    assert by_symbol["FASTUSDT"]["continuation_candidate"] is True
    assert by_symbol["FASTUSDT"]["trade_candidate"] is True

    assert by_symbol["RAVEUSDT"]["ignition_grade"] == "EXTREME"
    assert by_symbol["RAVEUSDT"]["ignition_candidate"] is True
    assert by_symbol["RAVEUSDT"]["trade_candidate"] is False
    assert by_symbol["RAVEUSDT"]["signal_priority"] == 3
    assert "EXTREME_MOVE" in by_symbol["RAVEUSDT"]["risk_flags"]
    assert by_symbol["RAVEUSDT"]["actionability_score"] >= 0.0

    rank_payload = next(row for row in rank_rows if row["symbol"] == "RAVEUSDT" and row["rank_scope"] == "all")[
        "payload"
    ]
    assert rank_payload["ignition_grade"] == "EXTREME"
    assert rank_payload["signal_priority"] == 3
    assert "actionability_score" in rank_payload


def test_build_snapshot_rows_uses_neutral_1h_volume_ratio_without_full_24h_history():
    snapshot_ts = datetime(2026, 1, 1, 6, tzinfo=timezone.utc)
    rows = []
    for hour in range(6):
        quote_volume = 10_000.0 if hour == 5 else 1000.0
        rows.append(
            {
                "asset_id": 1,
                "exchange": "binance",
                "symbol": "NEWUSDT",
                "base_asset": "NEW",
                "ts": pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=hour),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": quote_volume,
                "quote_volume": quote_volume,
            }
        )

    feature_rows, _ = build_snapshot_rows(pd.DataFrame(rows), snapshot_ts)
    row = feature_rows[0]

    assert row["volume_ratio_24h"] > 1.0
    assert row["volume_ratio_1h"] == 1.0
    assert row["volume_impulse_score"] == compute_volume_impulse_score(row)


def test_build_snapshot_rows_recomputes_actionability_after_cross_exchange_confirmation():
    snapshot_ts = datetime(2026, 2, 1, tzinfo=timezone.utc)
    rows = []
    exchanges = (("binance", 1), ("bybit", 100))
    for hour in range(24 * 31):
        ts = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=hour)
        btc_close = 100.0 + hour * 0.01
        eth_close = 100.0 + hour * 0.01
        rave_close = 100.0 + hour * 0.01
        if hour == 24 * 31 - 1:
            rave_close = 220.0
        elif hour > 24 * 31 - 25:
            rave_close = 125.0

        for exchange, offset in exchanges:
            for asset_id, symbol, close, quote_volume in (
                (offset, "BTCUSDT", btc_close, 1000.0),
                (offset + 1, "ETHUSDT", eth_close, 1000.0),
                (offset + 2, "RAVEUSDT", rave_close, 2500.0 if hour == 24 * 31 - 1 else 1000.0),
            ):
                rows.append(
                    {
                        "asset_id": asset_id,
                        "exchange": exchange,
                        "symbol": symbol,
                        "base_asset": symbol.removesuffix("USDT"),
                        "ts": ts,
                        "open": close,
                        "high": close * 1.01,
                        "low": close * 0.99,
                        "close": close,
                        "volume": quote_volume,
                        "quote_volume": quote_volume,
                        "open_interest": 1000.0 + hour,
                        "funding_rate": 0.0001,
                        "taker_buy_quote": quote_volume * 0.56,
                    }
                )

    feature_rows, _ = build_snapshot_rows(pd.DataFrame(rows), snapshot_ts)
    rave_rows = [row for row in feature_rows if row["symbol"] == "RAVEUSDT"]

    assert len(rave_rows) == 2
    for row in rave_rows:
        assert row["ignition_grade"] is not None or row["continuation_grade"] is not None
        assert row["cross_exchange_confirmed"] is True
        without_cross = evaluate_signal_v2(dict(row, cross_exchange_confirmed=False)).actionability_score
        assert row["actionability_score"] > without_cross


def test_write_run_once_snapshots_preserves_rank_payload(monkeypatch):
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
            }
        ]
    )
    inserted = []

    monkeypatch.setattr("altcoin_trend.scheduler._load_market_rows", lambda engine: market_rows)

    def fake_insert_rows(engine, table_name, rows):
        inserted.append((table_name, list(rows)))
        return len(inserted[-1][1])

    monkeypatch.setattr("altcoin_trend.scheduler.insert_rows", fake_insert_rows)

    from altcoin_trend.scheduler import write_run_once_snapshots

    write_run_once_snapshots(object(), snapshot_ts=datetime(2026, 1, 1, tzinfo=timezone.utc))

    feature_rows = next(rows for table_name, rows in inserted if table_name == "alt_signal.feature_snapshot")
    assert isinstance(feature_rows[0]["risk_flags"], Jsonb)
    assert feature_rows[0]["risk_flags"].obj == []

    rank_rows = next(rows for table_name, rows in inserted if table_name == "alt_signal.rank_snapshot")
    assert isinstance(rank_rows[0]["payload"], Jsonb)
    payload = rank_rows[0]["payload"].obj
    expected_payload_fields = {
        "trade_candidate": False,
        "continuation_candidate": False,
        "ignition_candidate": False,
        "continuation_grade": None,
        "ignition_grade": None,
        "signal_priority": 0,
        "risk_flags": [],
        "chase_risk_score": 0.0,
        "cross_exchange_confirmed": False,
    }
    assert {key: payload[key] for key in expected_payload_fields} == expected_payload_fields
    assert payload["actionability_score"] >= 0.0


def test_process_alerts_converts_payload_to_jsonb_before_insert(monkeypatch):
    inserted = []
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "altcoin_trend.scheduler.load_rank_rows",
        lambda engine, rank_scope, limit: [
            {
                "asset_id": 21,
                "exchange": "binance",
                "symbol": "RAVEUSDT",
                "tier": "monitor",
                "final_score": 63.0,
                "trend_score": 67.0,
                "volume_breakout_score": 35.0,
                "relative_strength_score": 92.0,
                "derivatives_score": 31.0,
                "quality_score": 100.0,
                "return_1h_pct": 12.5,
                "return_4h_pct": 10.0,
                "return_24h_percentile": 0.98,
                "veto_reason_codes": [],
            }
        ],
    )
    monkeypatch.setattr("altcoin_trend.scheduler._load_recent_alert_events", lambda engine, since: [])

    def fake_insert_rows(engine, table_name, rows):
        inserted.extend(rows)
        return len(rows)

    monkeypatch.setattr("altcoin_trend.scheduler.insert_rows", fake_insert_rows)

    inserted_count, sent_count = process_alerts(object(), now=now, cooldown_seconds=3600)

    assert inserted_count == 1
    assert sent_count == 0
    assert isinstance(inserted[0]["payload"], Jsonb)


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
    assert "fs.volume_ratio_1h" in captured["sql"]
    assert "fs.volume_impulse_score" in captured["sql"]
    assert "fs.return_24h_rank" in captured["sql"]
    assert "fs.return_7d_rank" in captured["sql"]
    assert "fs.continuation_grade" in captured["sql"]
    assert "fs.ignition_grade" in captured["sql"]
    assert "fs.signal_priority" in captured["sql"]
    assert "fs.risk_flags" in captured["sql"]
    assert "fs.chase_risk_score" in captured["sql"]
    assert "fs.actionability_score" in captured["sql"]
    assert "fs.cross_exchange_confirmed" in captured["sql"]
    assert "fs.veto_reason_codes" in captured["sql"]
    assert "LEFT JOIN alt_signal.feature_snapshot AS fs" not in captured["sql"]
    assert captured["params"] == {"rank_scope": "all", "limit": 30}


def test_load_trade_candidate_rows_selects_volume_ratio_1h():
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

    from altcoin_trend.scheduler import load_trade_candidate_rows

    load_trade_candidate_rows(Engine(), limit=30)

    assert "fs.volume_ratio_1h" in captured["sql"]
    assert captured["params"] == {"limit": 30}


def test_load_explain_row_selects_volume_ratio_1h():
    captured = {}

    class Result:
        def mappings(self):
            return self

        def first(self):
            return None

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

    from altcoin_trend.scheduler import load_explain_row

    load_explain_row(Engine(), symbol="solusdt", exchange="binance")

    assert "fs.volume_ratio_1h" in captured["sql"]
    assert captured["params"] == {"symbol": "SOLUSDT", "exchange": "binance"}


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
