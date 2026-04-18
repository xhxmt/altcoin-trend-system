from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import Engine, text

from altcoin_trend.db import insert_rows
from altcoin_trend.features.indicators import add_ema
from altcoin_trend.features.scoring import ScoreInput, compute_final_score
from altcoin_trend.signals.ranking import rank_scores


@dataclass(frozen=True)
class RunOnceResult:
    started_at: datetime
    status: str
    message: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _component_scores(group: pd.DataFrame) -> dict[str, float]:
    latest = group.iloc[-1]
    first = group.iloc[0]
    if first["close"] == 0:
        return_pct = 0.0
    else:
        return_pct = ((latest["close"] / first["close"]) - 1.0) * 100

    avg_quote_volume = float(group["quote_volume"].mean()) if not group.empty else 0.0
    volume_ratio = float(latest["quote_volume"]) / avg_quote_volume if avg_quote_volume > 0 else 0.0

    return {
        "trend_score": max(0.0, min(100.0, 50.0 + return_pct * 2.0)),
        "volume_breakout_score": max(0.0, min(100.0, volume_ratio * 50.0)),
        "relative_strength_score": 50.0,
        "derivatives_score": 50.0,
        "quality_score": max(0.0, min(100.0, len(group) / 60 * 100.0)),
    }


def build_snapshot_rows(market_rows: pd.DataFrame, snapshot_ts: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if market_rows.empty:
        return [], []

    working = market_rows.copy()
    working["ts"] = pd.to_datetime(working["ts"], utc=True)
    feature_rows: list[dict[str, Any]] = []

    for asset_id, group in working.sort_values(["asset_id", "ts"]).groupby("asset_id"):
        group = add_ema(group, column="close", span=20, output="ema20_1m")
        latest = group.iloc[-1]
        scores = _component_scores(group)
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


def _load_market_rows(engine: Engine, lookback_rows: int = 10_000) -> pd.DataFrame:
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
            m.taker_buy_quote
        FROM alt_core.market_1m AS m
        JOIN alt_core.asset_master AS a ON a.asset_id = m.asset_id
        ORDER BY m.ts DESC
        LIMIT :limit
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, {"limit": lookback_rows})
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
            ts,
            rank_scope,
            rank,
            asset_id,
            symbol,
            base_asset,
            final_score,
            tier,
            primary_reason
        FROM alt_signal.rank_snapshot
        WHERE rank_scope = :rank_scope
          AND ts = (
              SELECT MAX(ts)
              FROM alt_signal.rank_snapshot
              WHERE rank_scope = :rank_scope
          )
        ORDER BY rank
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
