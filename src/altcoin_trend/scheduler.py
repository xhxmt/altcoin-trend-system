from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

import pandas as pd
from psycopg.types.json import Jsonb
from sqlalchemy import Engine, text

from altcoin_trend.db import insert_rows
from altcoin_trend.features.derivatives import compute_derivatives_features
from altcoin_trend.features.indicators import add_ema, adx, atr
from altcoin_trend.features.relative_strength import (
    RelativeStrengthFeature,
    build_relative_strength_features_from_returns,
)
from altcoin_trend.features.resample import resample_market_1m
from altcoin_trend.features.scoring import ScoreInput, compute_final_score, max_tier
from altcoin_trend.signals.alerts import MAX_SIGNAL_V2_COOLDOWN_SECONDS, build_alert_event_rows
from altcoin_trend.signals.ranking import rank_scores
from altcoin_trend.signals.telegram import TelegramClient
from altcoin_trend.signals.v2 import compute_volume_impulse_score, evaluate_signal_v2


logger = logging.getLogger(__name__)
DEFAULT_STALE_MARKET_SECONDS = 3600


@dataclass(frozen=True)
class RunOnceResult:
    started_at: datetime
    status: str
    message: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _none_if_missing(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return value
    return value


def _component_scores(
    group: pd.DataFrame,
    timeframe_features: dict[str, Any] | None = None,
    relative_strength_score: float = 50.0,
    derivatives_score: float = 50.0,
) -> dict[str, float]:
    timeframe_features = timeframe_features or {}
    latest = group.iloc[-1]
    first = group.iloc[0]
    latest_close = float(latest["close"])
    if first["close"] == 0:
        return_pct = 0.0
    else:
        return_pct = ((latest_close / first["close"]) - 1.0) * 100

    avg_quote_volume = float(group["quote_volume"].mean()) if not group.empty else 0.0
    volume_ratio = float(latest["quote_volume"]) / avg_quote_volume if avg_quote_volume > 0 else 0.0
    volume_ratio_4h = timeframe_features.get("volume_ratio_4h")
    effective_volume_ratio = float(volume_ratio_4h) if volume_ratio_4h is not None else volume_ratio

    ema20_4h = timeframe_features.get("ema20_4h")
    ema60_4h = timeframe_features.get("ema60_4h")
    ema20_1d = timeframe_features.get("ema20_1d")
    ema60_1d = timeframe_features.get("ema60_1d")
    adx14_4h = timeframe_features.get("adx14_4h")
    breakout_20d = bool(timeframe_features.get("breakout_20d"))
    return_7d = timeframe_features.get("return_7d")
    return_30d = timeframe_features.get("return_30d")

    trend_score = 20.0
    extension_penalty = 0.0
    if ema20_4h is not None and latest_close > float(ema20_4h):
        trend_score += 25.0
        if float(ema20_4h) > 0:
            extension_pct = ((latest_close / float(ema20_4h)) - 1.0) * 100.0
            trend_score += max(0.0, min(15.0, extension_pct * 2.0))
            if extension_pct > 18.0:
                extension_penalty = min(35.0, (extension_pct - 18.0) * 3.0)
    if ema20_4h is not None and ema60_4h is not None and float(ema20_4h) > float(ema60_4h):
        trend_score += 25.0
    if adx14_4h is not None:
        trend_score += max(0.0, min(20.0, float(adx14_4h) / 40.0 * 20.0))
    if ema20_1d is not None and ema60_1d is not None and float(ema20_1d) > float(ema60_1d):
        trend_score += 10.0
    if return_7d is not None:
        trend_score += max(0.0, min(8.0, float(return_7d) / 4.0))
    if return_30d is not None:
        trend_score += max(0.0, min(7.0, float(return_30d) / 8.0))
    trend_score += max(0.0, min(10.0, return_pct / 3.0))

    volume_breakout_score = max(0.0, min(80.0, effective_volume_ratio * 40.0))
    if breakout_20d:
        volume_breakout_score += 20.0

    return {
        "trend_score": max(0.0, min(100.0, trend_score) - extension_penalty),
        "volume_breakout_score": max(0.0, min(100.0, volume_breakout_score)),
        "relative_strength_score": max(0.0, min(100.0, float(relative_strength_score))),
        "derivatives_score": max(0.0, min(100.0, float(derivatives_score))),
        "quality_score": max(0.0, min(100.0, len(group) / 60 * 100.0)),
    }


def _frame_with_required_columns(group: pd.DataFrame) -> pd.DataFrame:
    working = group.copy()
    for column, default in (("trade_count", 0), ("volume", 0.0), ("quote_volume", 0.0)):
        if column not in working.columns:
            working[column] = default
    return working


def _latest_value(frame: pd.DataFrame, column: str) -> float | None:
    if frame.empty or column not in frame.columns:
        return None
    value = frame[column].iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def _return_pct_since(ordered: pd.DataFrame, hours: int) -> float | None:
    if ordered.empty:
        return None
    latest = ordered.iloc[-1]
    latest_close = float(latest["close"])
    anchor_ts = latest["ts"] - pd.Timedelta(hours=hours)
    history = ordered[ordered["ts"] <= anchor_ts]
    if history.empty:
        return None
    anchor_close = float(history.iloc[-1]["close"])
    if anchor_close <= 0:
        return None
    return ((latest_close / anchor_close) - 1.0) * 100.0


def _trailing_volume_ratio_24h(ordered: pd.DataFrame) -> float | None:
    if ordered.empty or "quote_volume" not in ordered.columns:
        return None
    latest_ts = ordered["ts"].iloc[-1]
    current_start = latest_ts - pd.Timedelta(hours=1)
    lookback_start = latest_ts - pd.Timedelta(hours=24)
    current_volume = float(ordered[ordered["ts"] > current_start]["quote_volume"].sum())
    lookback_volume = float(ordered[ordered["ts"] > lookback_start]["quote_volume"].sum())
    average_hourly_volume = lookback_volume / 24.0 if lookback_volume > 0 else 0.0
    if average_hourly_volume <= 0:
        return None
    return current_volume / average_hourly_volume


def _higher_timeframe_features(group: pd.DataFrame) -> dict[str, Any]:
    working = _frame_with_required_columns(group)
    features: dict[str, Any] = {
        "return_1h_pct": None,
        "return_4h_pct": None,
        "return_24h_pct": None,
        "return_7d_pct": None,
        "return_30d_pct": None,
        "volume_ratio_1h": None,
        "volume_ratio_24h": None,
        "ema20_4h": None,
        "ema60_4h": None,
        "ema20_1d": None,
        "ema60_1d": None,
        "adx14_4h": None,
        "atr14_4h": None,
        "volume_ratio_4h": None,
        "breakout_20d": False,
        "return_7d": None,
        "return_30d": None,
    }

    bars_4h = resample_market_1m(working, "4h")
    if not bars_4h.empty:
        bars_4h = add_ema(bars_4h, column="close", span=20, output="ema20")
        bars_4h = add_ema(bars_4h, column="close", span=60, output="ema60")
        bars_4h["atr14"] = atr(bars_4h, window=14)
        bars_4h["adx14"] = adx(bars_4h, window=14)
        avg_volume_4h = float(bars_4h["volume"].tail(20).mean())
        latest_volume_4h = float(bars_4h["volume"].iloc[-1])
        features.update(
            {
                "ema20_4h": _latest_value(bars_4h, "ema20"),
                "ema60_4h": _latest_value(bars_4h, "ema60"),
                "atr14_4h": _latest_value(bars_4h, "atr14"),
                "adx14_4h": _latest_value(bars_4h, "adx14"),
                "volume_ratio_4h": latest_volume_4h / avg_volume_4h if avg_volume_4h > 0 else None,
            }
        )

    bars_1d = resample_market_1m(working, "1d")
    if not bars_1d.empty:
        bars_1d = add_ema(bars_1d, column="close", span=20, output="ema20")
        bars_1d = add_ema(bars_1d, column="close", span=60, output="ema60")
        features["ema20_1d"] = _latest_value(bars_1d, "ema20")
        features["ema60_1d"] = _latest_value(bars_1d, "ema60")
        if len(bars_1d) > 20:
            previous_high = float(bars_1d["high"].iloc[-21:-1].max())
            features["breakout_20d"] = float(bars_1d["close"].iloc[-1]) > previous_high

    if not working.empty:
        ordered = working.sort_values("ts")
        for hours, key in (
            (1, "return_1h_pct"),
            (4, "return_4h_pct"),
            (24, "return_24h_pct"),
            (24 * 7, "return_7d_pct"),
            (24 * 30, "return_30d_pct"),
        ):
            features[key] = _return_pct_since(ordered, hours)
        features["volume_ratio_24h"] = _trailing_volume_ratio_24h(ordered)
        latest_ts = ordered["ts"].iloc[-1]
        lookback_start = latest_ts - pd.Timedelta(hours=24)
        features["volume_ratio_1h"] = (
            features["volume_ratio_24h"] if ordered["ts"].min() <= lookback_start else 1.0
        )
        latest = ordered.iloc[-1]
        latest_close = float(latest["close"])
        for days, key in ((7, "return_7d"), (30, "return_30d")):
            anchor_ts = latest["ts"] - pd.Timedelta(days=days)
            history = ordered[ordered["ts"] <= anchor_ts]
            if history.empty:
                continue
            anchor_close = float(history.iloc[-1]["close"])
            if anchor_close > 0:
                features[key] = ((latest_close / anchor_close) - 1.0) * 100.0

    return features


def _assign_return_percentiles_and_ranks(feature_rows: list[dict[str, Any]]) -> None:
    if not feature_rows:
        return
    frame = pd.DataFrame(feature_rows)
    for source_column, percentile_column, rank_column in (
        ("return_24h_pct", "return_24h_percentile", "return_24h_rank"),
        ("return_7d_pct", "return_7d_percentile", "return_7d_rank"),
        ("return_30d_pct", "return_30d_percentile", "return_30d_rank"),
    ):
        if source_column not in frame.columns:
            for row in feature_rows:
                row[percentile_column] = None
                row[rank_column] = None
            continue
        frame[percentile_column] = frame.groupby("exchange")[source_column].rank(pct=True)
        frame[rank_column] = frame.groupby("exchange")[source_column].rank(method="min", ascending=False)
        for index, row in enumerate(feature_rows):
            percentile = frame.iloc[index][percentile_column]
            rank = frame.iloc[index][rank_column]
            row[percentile_column] = None if pd.isna(percentile) else float(percentile)
            row[rank_column] = None if pd.isna(rank) else int(rank)


def _apply_signal_v2_result(row: dict[str, Any]) -> None:
    result = evaluate_signal_v2(row)
    row["continuation_grade"] = result.continuation_grade
    row["ignition_grade"] = result.ignition_grade
    row["reacceleration_grade"] = result.reacceleration_grade
    row["ultra_high_conviction"] = result.ultra_high_conviction
    row["signal_priority"] = result.signal_priority
    row["risk_flags"] = list(result.risk_flags)
    row["chase_risk_score"] = result.chase_risk_score
    row["actionability_score"] = result.actionability_score
    row["continuation_candidate"] = result.continuation_grade is not None
    row["ignition_candidate"] = result.ignition_grade is not None
    row["trade_candidate"] = result.continuation_grade is not None


def _asset_groups_from_market_rows(market_rows: pd.DataFrame) -> Iterable[pd.DataFrame]:
    if market_rows.empty:
        return

    working = market_rows.copy()
    working["ts"] = pd.to_datetime(working["ts"], utc=True)
    for _, group in working.sort_values(["asset_id", "ts"]).groupby("asset_id"):
        yield group


def build_snapshot_rows_from_groups(
    asset_groups: Iterable[pd.DataFrame],
    snapshot_ts: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    partial_rows: list[dict[str, Any]] = []
    relative_strength_inputs: list[dict[str, Any]] = []

    for group in asset_groups:
        if group.empty:
            continue

        group = group.copy()
        group["ts"] = pd.to_datetime(group["ts"], utc=True)
        group = group.sort_values("ts")
        group = add_ema(group, column="close", span=20, output="ema20_1m")
        latest = group.iloc[-1]
        timeframe_features = _higher_timeframe_features(group)
        snapshot_timeframe_features = {
            key: value for key, value in timeframe_features.items() if key not in {"return_7d", "return_30d"}
        }
        derivatives = compute_derivatives_features(group)
        scores = _component_scores(
            group,
            timeframe_features,
            relative_strength_score=50.0,
            derivatives_score=derivatives.derivatives_score,
        )
        asset_id = int(latest["asset_id"])
        relative_strength_inputs.append(
            {
                "asset_id": asset_id,
                "exchange": str(latest["exchange"]),
                "symbol": str(latest["symbol"]).upper(),
                "return_7d": timeframe_features.get("return_7d"),
                "return_30d": timeframe_features.get("return_30d"),
            }
        )
        partial_rows.append(
            {
                "_scores": scores,
                "ts": snapshot_ts,
                "asset_id": asset_id,
                "exchange": latest["exchange"],
                "symbol": latest["symbol"],
                "base_asset": latest["base_asset"],
                "close": float(latest["close"]),
                "ema20_1m": float(latest["ema20_1m"]),
                **snapshot_timeframe_features,
                "oi_delta_1h": derivatives.oi_delta_1h,
                "oi_delta_4h": derivatives.oi_delta_4h,
                "funding_zscore": derivatives.funding_zscore,
                "taker_buy_sell_ratio": derivatives.taker_buy_sell_ratio,
                "veto_reason_codes": [],
                "volume_impulse_score": 0.0,
                "return_24h_rank": None,
                "return_7d_rank": None,
                "return_30d_rank": None,
                "continuation_grade": None,
                "ignition_grade": None,
                "reacceleration_grade": None,
                "ultra_high_conviction": False,
                "signal_priority": 0,
                "risk_flags": [],
                "chase_risk_score": 0.0,
                "actionability_score": 0.0,
                "cross_exchange_confirmed": False,
                "continuation_candidate": False,
                "ignition_candidate": False,
                "trade_candidate": False,
            }
        )

    if not partial_rows:
        return [], []

    relative_strength_by_asset = build_relative_strength_features_from_returns(relative_strength_inputs)
    feature_rows: list[dict[str, Any]] = []

    for partial_row in partial_rows:
        asset_id = int(partial_row["asset_id"])
        relative_strength = relative_strength_by_asset.get(
            asset_id,
            RelativeStrengthFeature(
                return_7d=None,
                return_30d=None,
                rs_btc_7d=None,
                rs_eth_7d=None,
                rs_btc_30d=None,
                rs_eth_30d=None,
                relative_strength_score=50.0,
            ),
        )
        scores = dict(partial_row.pop("_scores"))
        scores["relative_strength_score"] = relative_strength.relative_strength_score
        score_result = compute_final_score(ScoreInput(veto_reason_codes=[], **scores))
        feature_rows.append(
            {
                **partial_row,
                "rs_btc_7d": relative_strength.rs_btc_7d,
                "rs_eth_7d": relative_strength.rs_eth_7d,
                "rs_btc_30d": relative_strength.rs_btc_30d,
                "rs_eth_30d": relative_strength.rs_eth_30d,
                "trend_score": scores["trend_score"],
                "volume_breakout_score": scores["volume_breakout_score"],
                "relative_strength_score": scores["relative_strength_score"],
                "derivatives_score": scores["derivatives_score"],
                "quality_score": scores["quality_score"],
                "final_score": score_result.final_score,
                "tier": score_result.tier,
                "primary_reason": score_result.primary_reason,
            }
        )

    _assign_return_percentiles_and_ranks(feature_rows)
    for row in feature_rows:
        row["volume_impulse_score"] = compute_volume_impulse_score(row)
        _apply_signal_v2_result(row)

    signal_counts_by_symbol: dict[str, int] = {}
    for row in feature_rows:
        if (
            row["continuation_grade"] is not None
            or row["ignition_grade"] is not None
            or row["reacceleration_grade"] is not None
            or row["ultra_high_conviction"]
        ):
            symbol = str(row["symbol"])
            signal_counts_by_symbol[symbol] = signal_counts_by_symbol.get(symbol, 0) + 1

    for row in feature_rows:
        row["cross_exchange_confirmed"] = signal_counts_by_symbol.get(str(row["symbol"]), 0) >= 2
        _apply_signal_v2_result(row)
        if row["ignition_grade"] in {"A", "B"}:
            row["tier"] = max_tier(row["tier"], "watchlist")
        if row["ignition_grade"] == "EXTREME":
            row["tier"] = max_tier(row["tier"], "strong")
        if row["reacceleration_grade"] in {"A", "B"}:
            row["tier"] = max_tier(row["tier"], "watchlist")

    rank_rows: list[dict[str, Any]] = []
    for scope, rows in (("all", feature_rows),):
        rank_rows.extend(_rank_rows_for_scope(rows, scope))
    for exchange, rows in pd.DataFrame(feature_rows).groupby("exchange"):
        rank_rows.extend(_rank_rows_for_scope(rows.to_dict("records"), str(exchange)))
    return feature_rows, rank_rows


def build_snapshot_rows(market_rows: pd.DataFrame, snapshot_ts: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return build_snapshot_rows_from_groups(_asset_groups_from_market_rows(market_rows), snapshot_ts)


def _rank_rows_for_scope(rows: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    ranked = rank_scores(rows, rank_scope=scope)
    return [
        {
            "ts": row["ts"],
            "rank_scope": row["rank_scope"],
            "rank": row["rank"],
            "asset_id": row["asset_id"],
            "symbol": row["symbol"],
            "base_asset": row["base_asset"],
            "final_score": row["final_score"],
            "tier": row["tier"],
            "primary_reason": _none_if_missing(row["primary_reason"]),
            "payload": {
                "trade_candidate": bool(row.get("trade_candidate", False)),
                "continuation_candidate": bool(row.get("continuation_candidate", False)),
                "ignition_candidate": bool(row.get("ignition_candidate", False)),
                "ultra_high_conviction": bool(row.get("ultra_high_conviction", False)),
                "continuation_grade": _none_if_missing(row.get("continuation_grade")),
                "ignition_grade": _none_if_missing(row.get("ignition_grade")),
                "reacceleration_grade": _none_if_missing(row.get("reacceleration_grade")),
                "signal_priority": int(row.get("signal_priority", 0)),
                "risk_flags": list(row.get("risk_flags", [])),
                "chase_risk_score": float(row.get("chase_risk_score", 0.0)),
                "actionability_score": float(row.get("actionability_score", 0.0)),
                "cross_exchange_confirmed": bool(row.get("cross_exchange_confirmed", False)),
            },
        }
        for row in ranked
    ]


def _load_market_rows(engine: Engine, lookback_days: int = 31) -> pd.DataFrame:
    statement = text(
        """
        WITH latest AS (
            SELECT MAX(ts) AS max_ts
            FROM alt_core.market_1m
        )
        SELECT
            m.asset_id,
            m.exchange,
            m.symbol,
            a.base_asset,
            m.ts,
            m.open,
            m.high,
            m.low,
            m.close,
            m.volume,
            m.quote_volume,
            m.trade_count,
            m.taker_buy_base,
            m.taker_buy_quote,
            m.open_interest,
            m.funding_rate,
            m.long_short_ratio,
            m.buy_sell_ratio
        FROM alt_core.market_1m AS m
        JOIN alt_core.asset_master AS a ON a.asset_id = m.asset_id
        CROSS JOIN latest
        WHERE latest.max_ts IS NOT NULL
          AND m.ts >= latest.max_ts - make_interval(days => :lookback_days)
        ORDER BY m.asset_id, m.ts
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, {"lookback_days": lookback_days})
        return pd.DataFrame(result.mappings().all())


def _iter_market_row_groups(
    engine: Engine,
    lookback_days: int = 31,
    end_ts: datetime | None = None,
) -> Iterable[pd.DataFrame]:
    end_ts = end_ts or _utc_now()
    cutoff_ts = end_ts - timedelta(days=lookback_days)
    statement = text(
        """
        SELECT
            m.asset_id,
            m.exchange,
            m.symbol,
            a.base_asset,
            m.ts,
            m.open,
            m.high,
            m.low,
            m.close,
            m.volume,
            m.quote_volume,
            m.trade_count,
            m.taker_buy_base,
            m.taker_buy_quote,
            m.open_interest,
            m.funding_rate,
            m.long_short_ratio,
            m.buy_sell_ratio
        FROM alt_core.asset_master AS a
        JOIN LATERAL (
            SELECT *
            FROM alt_core.market_1m AS m
            WHERE m.asset_id = a.asset_id
              AND m.ts >= :cutoff_ts
              AND m.ts <= :end_ts
            ORDER BY m.ts
        ) AS m ON TRUE
        ORDER BY m.asset_id, m.ts
        """
    )
    current_asset_id: int | None = None
    current_rows: list[dict[str, Any]] = []
    with engine.connect() as connection:
        result = connection.execution_options(stream_results=True).execute(
            statement,
            {"cutoff_ts": cutoff_ts, "end_ts": end_ts},
        )
        for row in result.mappings():
            asset_id = int(row["asset_id"])
            if current_asset_id is not None and asset_id != current_asset_id:
                yield pd.DataFrame(current_rows)
                current_rows = []
            current_asset_id = asset_id
            current_rows.append(dict(row))

    if current_rows:
        yield pd.DataFrame(current_rows)


def _filter_fresh_market_row_groups(
    asset_groups: Iterable[pd.DataFrame],
    *,
    snapshot_ts: datetime,
    stale_market_seconds: int = DEFAULT_STALE_MARKET_SECONDS,
    stage: str = "snapshot",
) -> Iterable[pd.DataFrame]:
    cutoff_ts = pd.Timestamp(snapshot_ts) - pd.Timedelta(seconds=stale_market_seconds)
    total = 0
    stale = 0
    fresh = 0
    for group in asset_groups:
        total += 1
        if group.empty:
            stale += 1
            logger.info(
                "Stale market group filtered stage=%s symbol=unknown exchange=unknown last_market_ts=none threshold_seconds=%s reason=empty_group",
                stage,
                stale_market_seconds,
            )
            continue
        working = group.copy()
        working["ts"] = pd.to_datetime(working["ts"], utc=True)
        latest = working.iloc[working["ts"].argmax()]
        latest_ts = latest["ts"]
        symbol = str(latest.get("symbol", "unknown"))
        exchange = str(latest.get("exchange", "unknown"))
        if latest_ts < cutoff_ts:
            stale += 1
            logger.info(
                "Stale market group filtered stage=%s symbol=%s exchange=%s last_market_ts=%s threshold_seconds=%s cutoff_ts=%s",
                stage,
                symbol,
                exchange,
                latest_ts.isoformat(),
                stale_market_seconds,
                cutoff_ts.isoformat(),
            )
            continue
        fresh += 1
        yield group

    logger.info(
        "Stale market filtering summary stage=%s total_symbols=%s stale_symbols=%s fresh_symbols=%s threshold_seconds=%s",
        stage,
        total,
        stale,
        fresh,
        stale_market_seconds,
    )
    if total > 0 and stale == total:
        logger.warning(
            "Stale market filtering removed all symbols stage=%s total_symbols=%s threshold_seconds=%s",
            stage,
            total,
            stale_market_seconds,
        )
    elif total > 0 and stale / total >= 0.8:
        logger.warning(
            "Stale market filtering removed most symbols stage=%s total_symbols=%s stale_symbols=%s threshold_seconds=%s",
            stage,
            total,
            stale,
            stale_market_seconds,
        )


def write_run_once_snapshots(
    engine: Engine,
    snapshot_ts: datetime | None = None,
    lookback_days: int = 31,
    stale_market_seconds: int = DEFAULT_STALE_MARKET_SECONDS,
) -> tuple[int, int]:
    snapshot_ts = snapshot_ts or _utc_now()
    market_groups = _filter_fresh_market_row_groups(
        _iter_market_row_groups(engine, lookback_days=lookback_days, end_ts=snapshot_ts),
        snapshot_ts=snapshot_ts,
        stale_market_seconds=stale_market_seconds,
    )
    feature_rows, rank_rows = build_snapshot_rows_from_groups(
        market_groups,
        snapshot_ts,
    )
    if not feature_rows:
        return 0, 0

    feature_insert_rows = [
        {
            **{
                key: value
                for key, value in row.items()
                if key not in {"base_asset", "tier", "primary_reason"}
            },
            "risk_flags": Jsonb(row.get("risk_flags", [])),
        }
        for row in feature_rows
    ]
    features_written = insert_rows(engine, "alt_signal.feature_snapshot", feature_insert_rows)
    rank_insert_rows = [
        {
            **row,
            "payload": Jsonb(row["payload"]),
        }
        for row in rank_rows
    ]
    ranks_written = insert_rows(engine, "alt_signal.rank_snapshot", rank_insert_rows)
    return features_written, ranks_written


def load_rank_rows(engine: Engine, rank_scope: str = "all", limit: int = 30) -> list[dict[str, Any]]:
    statement = text(
        """
        SELECT
            r.ts,
            r.rank_scope,
            r.rank,
            r.asset_id,
            a.exchange,
            r.symbol,
            r.base_asset,
            r.final_score,
            r.tier,
            r.primary_reason,
            fs.trend_score,
            fs.volume_breakout_score,
            fs.relative_strength_score,
            fs.derivatives_score,
            fs.quality_score,
            fs.return_1h_pct,
            fs.return_4h_pct,
            fs.return_24h_pct,
            fs.return_7d_pct,
            fs.return_30d_pct,
            fs.volume_ratio_1h,
            fs.volume_ratio_24h,
            fs.return_24h_percentile,
            fs.return_7d_percentile,
            fs.volume_impulse_score,
            fs.return_24h_rank,
            fs.return_7d_rank,
            fs.continuation_grade,
            fs.ignition_grade,
            fs.reacceleration_grade,
            fs.signal_priority,
            fs.risk_flags,
            fs.chase_risk_score,
            fs.actionability_score,
            fs.cross_exchange_confirmed,
            fs.trade_candidate,
            fs.continuation_candidate,
            fs.ignition_candidate,
            fs.oi_delta_1h,
            fs.oi_delta_4h,
            fs.funding_zscore,
            fs.taker_buy_sell_ratio,
            fs.veto_reason_codes
        FROM alt_signal.rank_snapshot AS r
        JOIN alt_core.asset_master AS a USING (asset_id)
        JOIN alt_signal.feature_snapshot AS fs
          ON fs.asset_id = r.asset_id
         AND fs.ts = r.ts
        WHERE r.rank_scope = :rank_scope
          AND r.ts = (
              SELECT MAX(ts)
              FROM alt_signal.rank_snapshot
              WHERE rank_scope = :rank_scope
          )
        ORDER BY r.rank
        LIMIT :limit
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, {"rank_scope": rank_scope, "limit": limit})
        return [dict(row) for row in result.mappings().all()]


def load_trade_candidate_rows(engine: Engine, limit: int = 30) -> list[dict[str, Any]]:
    statement = text(
        """
        SELECT
            fs.ts,
            fs.asset_id,
            fs.exchange,
            fs.symbol,
            a.base_asset,
            fs.close,
            fs.final_score,
            fs.trade_candidate,
            fs.continuation_candidate,
            fs.ignition_candidate,
            fs.return_1h_pct,
            fs.return_4h_pct,
            fs.return_24h_pct,
            fs.return_7d_pct,
            fs.return_30d_pct,
            fs.volume_ratio_1h,
            fs.volume_ratio_24h,
            fs.return_24h_percentile,
            fs.return_7d_percentile,
            fs.volume_impulse_score,
            fs.return_24h_rank,
            fs.return_7d_rank,
            fs.continuation_grade,
            fs.ignition_grade,
            fs.reacceleration_grade,
            fs.signal_priority,
            fs.risk_flags,
            fs.chase_risk_score,
            fs.actionability_score,
            fs.cross_exchange_confirmed,
            COALESCE(r.tier, 'rejected') AS tier,
            COALESCE(r.rank, 0) AS rank
        FROM alt_signal.feature_snapshot AS fs
        JOIN alt_core.asset_master AS a ON a.asset_id = fs.asset_id
        LEFT JOIN alt_signal.rank_snapshot AS r
          ON r.asset_id = fs.asset_id
         AND r.ts = fs.ts
         AND r.rank_scope = 'all'
        WHERE fs.ts = (
              SELECT MAX(ts)
              FROM alt_signal.feature_snapshot
          )
          AND fs.trade_candidate = TRUE
        ORDER BY fs.final_score DESC, fs.return_24h_pct DESC
        LIMIT :limit
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, {"limit": limit})
        return [dict(row) for row in result.mappings().all()]


def load_opportunity_rows(engine: Engine, limit: int = 30) -> list[dict[str, Any]]:
    statement = text(
        """
        SELECT
            fs.ts,
            fs.asset_id,
            fs.exchange,
            fs.symbol,
            a.base_asset,
            fs.close,
            fs.final_score,
            fs.continuation_grade,
            fs.ignition_grade,
            fs.reacceleration_grade,
            fs.signal_priority,
            fs.risk_flags,
            fs.chase_risk_score,
            fs.actionability_score,
            fs.cross_exchange_confirmed,
            fs.return_1h_pct,
            fs.return_4h_pct,
            fs.return_24h_pct,
            fs.volume_ratio_1h,
            fs.volume_impulse_score
        FROM alt_signal.feature_snapshot AS fs
        JOIN alt_core.asset_master AS a ON a.asset_id = fs.asset_id
        WHERE fs.ts = (
              SELECT MAX(ts)
              FROM alt_signal.feature_snapshot
          )
          AND fs.signal_priority > 0
        ORDER BY fs.actionability_score DESC, fs.signal_priority DESC, fs.final_score DESC
        LIMIT :limit
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, {"limit": limit})
        return [dict(row) for row in result.mappings().all()]


def load_explain_row(engine: Engine, symbol: str, exchange: str) -> dict[str, Any] | None:
    statement = text(
        """
        SELECT
            fs.ts,
            fs.asset_id,
            fs.exchange,
            fs.symbol,
            a.base_asset,
            fs.close,
            fs.ema20_1m,
            fs.return_1h_pct,
            fs.return_4h_pct,
            fs.return_24h_pct,
            fs.return_7d_pct,
            fs.return_30d_pct,
            fs.volume_ratio_1h,
            fs.volume_ratio_4h,
            fs.volume_ratio_24h,
            fs.return_24h_percentile,
            fs.return_7d_percentile,
            fs.return_30d_percentile,
            fs.return_30d_rank,
            fs.volume_impulse_score,
            fs.return_24h_rank,
            fs.return_7d_rank,
            fs.breakout_20d,
            fs.continuation_grade,
            fs.ignition_grade,
            fs.reacceleration_grade,
            fs.signal_priority,
            fs.risk_flags,
            fs.chase_risk_score,
            fs.actionability_score,
            fs.cross_exchange_confirmed,
            fs.trade_candidate,
            fs.continuation_candidate,
            fs.ignition_candidate,
            fs.trend_score,
            fs.volume_breakout_score,
            fs.relative_strength_score,
            fs.derivatives_score,
            fs.quality_score,
            fs.rs_btc_7d,
            fs.rs_eth_7d,
            fs.rs_btc_30d,
            fs.rs_eth_30d,
            fs.oi_delta_1h,
            fs.oi_delta_4h,
            fs.funding_zscore,
            fs.taker_buy_sell_ratio,
            fs.final_score,
            COALESCE(r.tier, 'rejected') AS tier,
            COALESCE(r.primary_reason, '') AS primary_reason,
            fs.veto_reason_codes
        FROM alt_signal.feature_snapshot AS fs
        JOIN alt_core.asset_master AS a ON a.asset_id = fs.asset_id
        LEFT JOIN alt_signal.rank_snapshot AS r
          ON r.asset_id = fs.asset_id
         AND r.ts = fs.ts
         AND r.rank_scope = :exchange
        WHERE fs.symbol = :symbol
          AND fs.exchange = :exchange
        ORDER BY fs.ts DESC
        LIMIT 1
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, {"symbol": symbol.upper(), "exchange": exchange})
        row = result.mappings().first()
        return dict(row) if row is not None else None


def _load_recent_alert_events(engine: Engine, since: datetime) -> list[dict[str, Any]]:
    statement = text(
        """
        SELECT
            alert_id,
            ts,
            asset_id,
            symbol,
            alert_type,
            final_score,
            message,
            payload,
            delivery_status,
            delivery_error
        FROM alt_signal.alert_events
        WHERE ts >= :since
        ORDER BY ts DESC
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, {"since": since})
        return [dict(row) for row in result.mappings().all()]


def process_alerts(
    engine: Engine,
    now: datetime,
    cooldown_seconds: int,
    telegram_client: TelegramClient | None = None,
    rank_limit: int = 30,
    recent_since: datetime | None = None,
) -> tuple[int, int]:
    rank_rows = load_rank_rows(engine, rank_scope="all", limit=rank_limit)
    since = recent_since or now - timedelta(seconds=max(cooldown_seconds, MAX_SIGNAL_V2_COOLDOWN_SECONDS))
    recent_events = _load_recent_alert_events(engine, since)
    alert_rows = build_alert_event_rows(
        rank_rows=rank_rows,
        recent_events=recent_events,
        now=now,
        cooldown_seconds=cooldown_seconds,
    )
    sent_count = 0
    for row in alert_rows:
        if telegram_client is None:
            continue
        result = telegram_client.send_message(row["message"])
        if result.ok:
            row["delivery_status"] = "sent"
            sent_count += 1
        else:
            row["delivery_status"] = "failed"
            row["delivery_error"] = result.error
    alert_insert_rows = [
        {
            **row,
            "payload": Jsonb(row["payload"]),
        }
        for row in alert_rows
    ]
    inserted_count = insert_rows(engine, "alt_signal.alert_events", alert_insert_rows)
    return inserted_count, sent_count


def run_once_pipeline(
    step: Callable[[], str] | None = None,
    *,
    engine: Engine | None = None,
    now: datetime | None = None,
    snapshot_lookback_days: int = 31,
    stale_market_seconds: int = DEFAULT_STALE_MARKET_SECONDS,
) -> RunOnceResult:
    started_at = now or _utc_now()
    if step is None:
        if engine is None:
            return RunOnceResult(started_at=started_at, status="degraded", message="no pipeline step configured")

        features_written, ranks_written = write_run_once_snapshots(
            engine,
            snapshot_ts=started_at,
            lookback_days=snapshot_lookback_days,
            stale_market_seconds=stale_market_seconds,
        )
        if features_written == 0:
            return RunOnceResult(started_at=started_at, status="degraded", message="no market rows available")
        return RunOnceResult(
            started_at=started_at,
            status="healthy",
            message=f"features_written={features_written} ranks_written={ranks_written}",
        )

    message = step()
    return RunOnceResult(started_at=started_at, status="healthy", message=message)
