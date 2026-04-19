from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import Engine, text

from altcoin_trend.db import insert_rows
from altcoin_trend.features.derivatives import compute_derivatives_features
from altcoin_trend.features.indicators import add_ema, adx, atr
from altcoin_trend.features.relative_strength import RelativeStrengthFeature, build_relative_strength_features
from altcoin_trend.features.resample import resample_market_1m
from altcoin_trend.features.scoring import ScoreInput, compute_final_score
from altcoin_trend.signals.alerts import build_alert_event_rows
from altcoin_trend.signals.ranking import rank_scores
from altcoin_trend.signals.telegram import TelegramClient


@dataclass(frozen=True)
class RunOnceResult:
    started_at: datetime
    status: str
    message: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def _higher_timeframe_features(group: pd.DataFrame) -> dict[str, Any]:
    working = _frame_with_required_columns(group)
    features: dict[str, Any] = {
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


def build_snapshot_rows(market_rows: pd.DataFrame, snapshot_ts: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if market_rows.empty:
        return [], []

    working = market_rows.copy()
    working["ts"] = pd.to_datetime(working["ts"], utc=True)
    relative_strength_by_asset = build_relative_strength_features(working)
    feature_rows: list[dict[str, Any]] = []

    for asset_id, group in working.sort_values(["asset_id", "ts"]).groupby("asset_id"):
        group = add_ema(group, column="close", span=20, output="ema20_1m")
        latest = group.iloc[-1]
        timeframe_features = _higher_timeframe_features(group)
        snapshot_timeframe_features = {
            key: value for key, value in timeframe_features.items() if key not in {"return_7d", "return_30d"}
        }
        relative_strength = relative_strength_by_asset.get(
            int(asset_id),
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
        derivatives = compute_derivatives_features(group)
        scores = _component_scores(
            group,
            timeframe_features,
            relative_strength.relative_strength_score,
            derivatives.derivatives_score,
        )
        score_result = compute_final_score(ScoreInput(veto_reason_codes=[], **scores))
        feature_rows.append(
            {
                "ts": snapshot_ts,
                "asset_id": int(asset_id),
                "exchange": latest["exchange"],
                "symbol": latest["symbol"],
                "base_asset": latest["base_asset"],
                "close": float(latest["close"]),
                "ema20_1m": float(latest["ema20_1m"]),
                **snapshot_timeframe_features,
                "rs_btc_7d": relative_strength.rs_btc_7d,
                "rs_eth_7d": relative_strength.rs_eth_7d,
                "rs_btc_30d": relative_strength.rs_btc_30d,
                "rs_eth_30d": relative_strength.rs_eth_30d,
                "oi_delta_1h": derivatives.oi_delta_1h,
                "oi_delta_4h": derivatives.oi_delta_4h,
                "funding_zscore": derivatives.funding_zscore,
                "taker_buy_sell_ratio": derivatives.taker_buy_sell_ratio,
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

    rank_rows: list[dict[str, Any]] = []
    for scope, rows in (("all", feature_rows),):
        rank_rows.extend(_rank_rows_for_scope(rows, scope))
    for exchange, rows in pd.DataFrame(feature_rows).groupby("exchange"):
        rank_rows.extend(_rank_rows_for_scope(rows.to_dict("records"), str(exchange)))
    return feature_rows, rank_rows


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
            "primary_reason": row["primary_reason"] or None,
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


def write_run_once_snapshots(engine: Engine, snapshot_ts: datetime | None = None) -> tuple[int, int]:
    snapshot_ts = snapshot_ts or _utc_now()
    market_rows = _load_market_rows(engine)
    feature_rows, rank_rows = build_snapshot_rows(market_rows, snapshot_ts)
    if not feature_rows:
        return 0, 0

    feature_insert_rows = [
        {
            key: value
            for key, value in row.items()
            if key not in {"base_asset", "tier", "primary_reason"}
        }
        for row in feature_rows
    ]
    features_written = insert_rows(engine, "alt_signal.feature_snapshot", feature_insert_rows)
    ranks_written = insert_rows(engine, "alt_signal.rank_snapshot", rank_rows)
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
            fs.oi_delta_1h,
            fs.oi_delta_4h,
            fs.funding_zscore,
            fs.taker_buy_sell_ratio,
            fs.veto_reason_codes
        FROM alt_signal.rank_snapshot AS r
        JOIN alt_core.asset_master AS a USING (asset_id)
        LEFT JOIN alt_signal.feature_snapshot AS fs
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
) -> tuple[int, int]:
    rank_rows = load_rank_rows(engine, rank_scope="all", limit=rank_limit)
    since = now - timedelta(seconds=cooldown_seconds)
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
    inserted_count = insert_rows(engine, "alt_signal.alert_events", alert_rows)
    return inserted_count, sent_count


def run_once_pipeline(
    step: Callable[[], str] | None = None,
    *,
    engine: Engine | None = None,
    now: datetime | None = None,
) -> RunOnceResult:
    started_at = now or _utc_now()
    if step is None:
        if engine is None:
            return RunOnceResult(started_at=started_at, status="degraded", message="no pipeline step configured")

        features_written, ranks_written = write_run_once_snapshots(engine, snapshot_ts=started_at)
        if features_written == 0:
            return RunOnceResult(started_at=started_at, status="degraded", message="no market rows available")
        return RunOnceResult(
            started_at=started_at,
            status="healthy",
            message=f"features_written={features_written} ranks_written={ranks_written}",
        )

    message = step()
    return RunOnceResult(started_at=started_at, status="healthy", message=message)
