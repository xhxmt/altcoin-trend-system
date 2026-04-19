from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Engine, text

from altcoin_trend.signals.alerts import is_high_value_signal


@dataclass(frozen=True)
class HorizonStats:
    avg_return: float
    win_rate: float
    observations: int


@dataclass(frozen=True)
class BacktestSummary:
    signal_count: int
    average_score: float
    tier_counts: dict[str, int]
    exchange_counts: dict[str, int]
    horizon_stats: dict[str, HorizonStats]
    top_signals: list[dict[str, Any]]


def _coerce_utc_datetime(value: datetime) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_horizons(value: str) -> tuple[tuple[str, timedelta], ...]:
    horizons: list[tuple[str, timedelta]] = []
    for item in value.split(","):
        token = item.strip().lower()
        if not token:
            raise ValueError("Horizon values must not be empty")
        if token.endswith("h"):
            magnitude_text = token[:-1]
            unit = "h"
        elif token.endswith("d"):
            magnitude_text = token[:-1]
            unit = "d"
        else:
            raise ValueError(f"Unsupported horizon: {item}")
        if not magnitude_text.isdigit():
            raise ValueError(f"Unsupported horizon: {item}")
        magnitude = int(magnitude_text)
        if magnitude <= 0:
            raise ValueError(f"Unsupported horizon: {item}")
        delta = timedelta(hours=magnitude) if unit == "h" else timedelta(days=magnitude)
        horizons.append((f"{magnitude}{unit}", delta))
    if not horizons:
        raise ValueError("At least one horizon is required")
    return tuple(horizons)


def summarize_backtest(
    signals: Sequence[Mapping[str, Any]],
    returns_by_horizon: Mapping[str, Sequence[float]],
    limit: int,
) -> BacktestSummary:
    signal_rows = [dict(signal) for signal in signals]
    signal_count = len(signal_rows)
    average_score = round(
        sum(float(signal.get("final_score", 0.0)) for signal in signal_rows) / signal_count,
        4,
    ) if signal_rows else 0.0

    tier_counts = Counter(str(signal.get("tier", "rejected")) for signal in signal_rows)
    exchange_counts = Counter(str(signal.get("exchange", "unknown")) for signal in signal_rows)

    horizon_stats: dict[str, HorizonStats] = {}
    for horizon, values in returns_by_horizon.items():
        returns = list(values)
        if not returns:
            horizon_stats[horizon] = HorizonStats(avg_return=0.0, win_rate=0.0, observations=0)
            continue
        average_return = round(sum(returns) / len(returns), 6)
        win_rate = round(sum(1 for value in returns if value > 0) / len(returns) * 100.0, 2)
        horizon_stats[horizon] = HorizonStats(avg_return=average_return, win_rate=win_rate, observations=len(returns))

    signal_rows.sort(key=lambda row: float(row.get("final_score", 0.0)), reverse=True)
    top_signals = signal_rows[:limit]

    return BacktestSummary(
        signal_count=signal_count,
        average_score=average_score,
        tier_counts=dict(tier_counts),
        exchange_counts=dict(exchange_counts),
        horizon_stats=horizon_stats,
        top_signals=top_signals,
    )


def _fetch_snapshot_rows(
    engine: Engine,
    start: datetime,
    end: datetime,
    min_score: float,
) -> list[dict[str, Any]]:
    statement = text(
        """
        SELECT
            fs.ts,
            fs.asset_id,
            fs.exchange,
            fs.symbol,
            fs.close,
            fs.final_score,
            COALESCE(r.tier, 'rejected') AS tier,
            fs.trend_score,
            fs.volume_breakout_score,
            fs.relative_strength_score,
            fs.derivatives_score,
            fs.quality_score,
            fs.veto_reason_codes
        FROM alt_signal.feature_snapshot AS fs
        LEFT JOIN alt_signal.rank_snapshot AS r
          ON r.asset_id = fs.asset_id
         AND r.ts = fs.ts
         AND r.rank_scope = fs.exchange
        WHERE fs.ts >= :start
          AND fs.ts < :end
          AND fs.final_score >= :min_score
        ORDER BY fs.ts, fs.asset_id
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, {"start": start, "end": end, "min_score": min_score})
        return [dict(row) for row in result.mappings().all()]


def _fetch_forward_close(
    engine: Engine,
    asset_id: int,
    target_ts: datetime,
) -> dict[str, Any] | None:
    statement = text(
        """
        SELECT
            m.ts,
            m.close
        FROM alt_core.market_1m AS m
        WHERE m.asset_id = :asset_id
          AND m.ts >= :target_ts
        ORDER BY m.ts ASC
        LIMIT 1
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, {"asset_id": asset_id, "target_ts": target_ts})
        row = result.mappings().first()
        return dict(row) if row is not None else None


def run_signal_backtest(
    engine: Engine,
    start: datetime,
    end: datetime,
    min_score: float,
    horizons: Sequence[tuple[str, timedelta]],
    high_value_only: bool,
    limit: int,
) -> BacktestSummary:
    start_utc = _coerce_utc_datetime(start)
    end_utc = _coerce_utc_datetime(end)
    if start_utc >= end_utc:
        raise ValueError("start must be earlier than end")
    snapshot_rows = _fetch_snapshot_rows(engine, start_utc, end_utc, min_score)

    signals: list[dict[str, Any]] = []
    returns_by_horizon: dict[str, list[float]] = defaultdict(list)

    for row in snapshot_rows:
        signal = dict(row)
        if high_value_only and not is_high_value_signal(signal):
            continue
        signals.append(signal)
        snapshot_close = float(signal["close"])
        signal_ts = _coerce_utc_datetime(signal["ts"])
        asset_id = int(signal["asset_id"])
        for horizon_label, delta in horizons:
            forward_row = _fetch_forward_close(engine, asset_id=asset_id, target_ts=signal_ts + delta)
            if forward_row is None:
                continue
            forward_close = float(forward_row["close"])
            if snapshot_close <= 0:
                continue
            returns_by_horizon[horizon_label].append((forward_close / snapshot_close) - 1.0)

    for horizon_label, _ in horizons:
        returns_by_horizon.setdefault(horizon_label, [])

    return summarize_backtest(signals, returns_by_horizon, limit=limit)
