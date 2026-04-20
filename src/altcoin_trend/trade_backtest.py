from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import Engine, text

from altcoin_trend.features.resample import resample_market_1m
from altcoin_trend.signals.trade_candidate import is_trade_candidate


@dataclass(frozen=True)
class TradeCandidateBacktestSummary:
    signal_count: int
    hit_count: int
    precision: float
    avg_future_max_return: float
    median_future_max_return: float
    best_future_max_return: float
    top_signals: list[dict[str, Any]]


def _coerce_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _prepare_feature_frame(bars_1h: pd.DataFrame) -> pd.DataFrame:
    if bars_1h.empty:
        return bars_1h.copy()
    frame = bars_1h.copy()
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame = frame.sort_values(["asset_id", "ts"]).reset_index(drop=True)
    grouped = frame.groupby("asset_id", group_keys=False)

    frame["return_1h_pct"] = grouped["close"].pct_change(1) * 100.0
    frame["return_4h_pct"] = grouped["close"].pct_change(4) * 100.0
    frame["return_24h_pct"] = grouped["close"].pct_change(24) * 100.0
    frame["return_7d_pct"] = grouped["close"].pct_change(24 * 7) * 100.0
    frame["return_30d_pct"] = grouped["close"].pct_change(24 * 30) * 100.0
    rolling_volume = grouped["quote_volume"].rolling(24, min_periods=12).mean().reset_index(level=0, drop=True)
    frame["volume_ratio_24h"] = frame["quote_volume"] / rolling_volume
    frame["future_high_1h"] = grouped["high"].shift(-1)
    frame["future_max_return_1h"] = (frame["future_high_1h"] / frame["close"]) - 1.0
    frame["quality_score"] = 100.0
    frame["veto_reason_codes"] = [[] for _ in range(len(frame))]
    frame["return_24h_percentile"] = frame.groupby(["exchange", "ts"])["return_24h_pct"].rank(pct=True)
    frame["return_7d_percentile"] = frame.groupby(["exchange", "ts"])["return_7d_pct"].rank(pct=True)
    frame["trade_candidate"] = [is_trade_candidate(row) for row in frame.to_dict("records")]
    return frame


def evaluate_trade_candidate_bars(
    bars_1h: pd.DataFrame,
    start: datetime,
    end: datetime,
    target_return: float,
    limit: int,
) -> TradeCandidateBacktestSummary:
    start_utc = _coerce_utc_datetime(start)
    end_utc = _coerce_utc_datetime(end)
    if start_utc >= end_utc:
        raise ValueError("start must be earlier than end")

    features = _prepare_feature_frame(bars_1h)
    if features.empty:
        return TradeCandidateBacktestSummary(0, 0, 0.0, 0.0, 0.0, 0.0, [])
    window = features[(features["ts"] >= start_utc) & (features["ts"] < end_utc)]
    signals = window[window["trade_candidate"] & window["future_max_return_1h"].notna()].copy()
    if signals.empty:
        return TradeCandidateBacktestSummary(0, 0, 0.0, 0.0, 0.0, 0.0, [])

    hit_count = int((signals["future_max_return_1h"] >= target_return).sum())
    signal_count = int(len(signals))
    top = signals.sort_values("future_max_return_1h", ascending=False).head(limit)
    top_signals = [
        {
            "ts": row["ts"],
            "exchange": row["exchange"],
            "symbol": row["symbol"],
            "close": float(row["close"]),
            "future_max_return_1h": float(row["future_max_return_1h"]),
            "return_1h_pct": float(row["return_1h_pct"]),
            "return_4h_pct": float(row["return_4h_pct"]),
            "return_24h_pct": float(row["return_24h_pct"]),
            "volume_ratio_24h": float(row["volume_ratio_24h"]),
        }
        for row in top.to_dict("records")
    ]
    return TradeCandidateBacktestSummary(
        signal_count=signal_count,
        hit_count=hit_count,
        precision=round(hit_count / signal_count * 100.0, 2),
        avg_future_max_return=round(float(signals["future_max_return_1h"].mean()), 6),
        median_future_max_return=round(float(signals["future_max_return_1h"].median()), 6),
        best_future_max_return=round(float(signals["future_max_return_1h"].max()), 6),
        top_signals=top_signals,
    )


def _fetch_market_rows(engine: Engine, exchange: str, start: datetime, end: datetime) -> pd.DataFrame:
    statement = text(
        """
        SELECT
            m.asset_id,
            m.exchange,
            m.symbol,
            m.ts,
            m.open,
            m.high,
            m.low,
            m.close,
            m.volume,
            m.quote_volume,
            m.trade_count
        FROM alt_core.market_1m AS m
        WHERE m.exchange = :exchange
          AND m.ts >= :start
          AND m.ts < :end
        ORDER BY m.asset_id, m.ts
        """
    )
    with engine.begin() as connection:
        result = connection.execute(statement, {"exchange": exchange, "start": start, "end": end})
        return pd.DataFrame(result.mappings().all())


def run_trade_candidate_backtest(
    engine: Engine,
    exchange: str,
    start: datetime,
    end: datetime,
    target_return: float,
    limit: int,
) -> TradeCandidateBacktestSummary:
    start_utc = _coerce_utc_datetime(start)
    end_utc = _coerce_utc_datetime(end)
    market_start = start_utc - timedelta(days=31)
    market_end = end_utc + timedelta(hours=1)
    market_rows = _fetch_market_rows(engine, exchange=exchange, start=market_start, end=market_end)
    if market_rows.empty:
        return TradeCandidateBacktestSummary(0, 0, 0.0, 0.0, 0.0, 0.0, [])

    bars: list[pd.DataFrame] = []
    for _, group in market_rows.groupby("asset_id"):
        resampled = resample_market_1m(group, "1h")
        if resampled.empty:
            continue
        latest = group.iloc[-1]
        resampled["asset_id"] = latest["asset_id"]
        resampled["exchange"] = latest["exchange"]
        resampled["symbol"] = latest["symbol"]
        bars.append(resampled)
    if not bars:
        return TradeCandidateBacktestSummary(0, 0, 0.0, 0.0, 0.0, 0.0, [])
    return evaluate_trade_candidate_bars(
        pd.concat(bars, ignore_index=True),
        start=start_utc,
        end=end_utc,
        target_return=target_return,
        limit=limit,
    )
