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


def _empty_signal_v2_group_entry() -> dict[str, float | int]:
    return {
        "signal_count": 0,
        "hit_5pct_rate": 0.0,
        "hit_10pct_rate": 0.0,
        "hit_10pct_before_drawdown_8pct_rate": 0.0,
        "avg_mfe_1h_pct": 0.0,
        "avg_mfe_4h_pct": 0.0,
        "avg_mfe_24h_pct": 0.0,
        "avg_mae_1h_pct": 0.0,
        "avg_mae_4h_pct": 0.0,
        "avg_mae_24h_pct": 0.0,
        "median_time_to_hit_10pct_minutes": 0.0,
    }


def _empty_signal_v2_group_summary() -> dict[str, dict[str, float | int]]:
    return {
        "continuation_A": _empty_signal_v2_group_entry(),
        "continuation_B": _empty_signal_v2_group_entry(),
        "ignition_A": _empty_signal_v2_group_entry(),
        "ignition_B": _empty_signal_v2_group_entry(),
        "ignition_EXTREME": _empty_signal_v2_group_entry(),
        "cross_exchange_confirmed": _empty_signal_v2_group_entry(),
        "single_exchange_triggered": _empty_signal_v2_group_entry(),
        "high_chase_risk": _empty_signal_v2_group_entry(),
        "low_or_medium_chase_risk": _empty_signal_v2_group_entry(),
    }


def _coerce_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _coerce_utc_timestamp(value: pd.Timestamp | datetime | str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None or ts.utcoffset() is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _empty_forward_path_labels() -> dict[str, Any]:
    return {
        "mfe_1h_pct": 0.0,
        "mfe_4h_pct": 0.0,
        "mfe_24h_pct": 0.0,
        "mae_1h_pct": 0.0,
        "mae_4h_pct": 0.0,
        "mae_24h_pct": 0.0,
        "hit_5pct_before_drawdown_5pct": False,
        "hit_10pct_before_drawdown_8pct": False,
        "time_to_hit_5pct_minutes": None,
        "time_to_hit_10pct_minutes": None,
    }


def compute_forward_path_labels(
    signal_ts: pd.Timestamp | datetime,
    signal_close: float,
    future_rows: pd.DataFrame,
) -> dict[str, Any]:
    try:
        close = float(signal_close)
    except (TypeError, ValueError):
        return _empty_forward_path_labels()
    if pd.isna(close) or close <= 0 or future_rows.empty:
        return _empty_forward_path_labels()

    labels = _empty_forward_path_labels()

    signal_ts_utc = _coerce_utc_timestamp(signal_ts)
    future = future_rows.copy()
    future["ts"] = pd.to_datetime(future["ts"], utc=True)
    future = future.sort_values("ts").reset_index(drop=True)

    windows = {
        "1h": signal_ts_utc + pd.Timedelta(hours=1),
        "4h": signal_ts_utc + pd.Timedelta(hours=4),
        "24h": signal_ts_utc + pd.Timedelta(hours=24),
    }

    for window_name, window_end in windows.items():
        window_rows = future[future["ts"] <= window_end]
        if window_rows.empty:
            continue
        window_high = float(window_rows["high"].max())
        window_low = float(window_rows["low"].min())
        labels[f"mfe_{window_name}_pct"] = round((window_high / close - 1.0) * 100.0, 6)
        labels[f"mae_{window_name}_pct"] = round((1.0 - window_low / close) * 100.0, 6)

    for target_pct, drawdown_pct, hit_key, time_key in (
        (0.05, 0.05, "hit_5pct_before_drawdown_5pct", "time_to_hit_5pct_minutes"),
        (0.10, 0.08, "hit_10pct_before_drawdown_8pct", "time_to_hit_10pct_minutes"),
    ):
        target_price = close * (1.0 + target_pct)
        drawdown_price = close * (1.0 - drawdown_pct)
        for row in future.itertuples(index=False):
            row_high = float(row.high)
            row_low = float(row.low)
            if row_high >= target_price:
                hit_time = _coerce_utc_timestamp(row.ts)
                labels[hit_key] = True
                labels[time_key] = round((hit_time - signal_ts_utc).total_seconds() / 60.0, 6)
                break
            if row_low <= drawdown_price:
                labels[hit_key] = False
                labels[time_key] = None
                break
        else:
            labels[hit_key] = False
            labels[time_key] = None

    return labels


def summarize_signal_v2_groups(signals: pd.DataFrame) -> dict[str, dict[str, float | int]]:
    summary = _empty_signal_v2_group_summary()
    if signals.empty:
        return summary

    def _group_frame(mask: pd.Series) -> pd.DataFrame:
        return signals[mask.fillna(False)]

    def _as_numeric(group: pd.DataFrame, column: str) -> pd.Series:
        if column not in group.columns:
            return pd.Series(dtype="float64")
        return pd.to_numeric(group[column], errors="coerce")

    def _average(group: pd.DataFrame, column: str) -> float:
        series = _as_numeric(group, column).dropna()
        if series.empty:
            return 0.0
        return round(float(series.mean()), 6)

    def _rate_from_column_or_threshold(group: pd.DataFrame, column_candidates: tuple[str, ...], threshold: float) -> float:
        for column in column_candidates:
            if column in group.columns:
                series = _as_numeric(group, column).fillna(0.0)
                if series.empty:
                    return 0.0
                return round(float((series > 0).mean() * 100.0), 6)
        series = _as_numeric(group, "mfe_1h_pct").fillna(0.0)
        if series.empty:
            return 0.0
        return round(float((series >= threshold).mean() * 100.0), 6)

    def _median_minutes(group: pd.DataFrame, column: str) -> float:
        series = _as_numeric(group, column).dropna()
        if series.empty:
            return 0.0
        median = float(series.median())
        return 0.0 if pd.isna(median) else round(median, 6)

    def _populate_group(name: str, mask: pd.Series) -> None:
        group = _group_frame(mask)
        if group.empty:
            summary[name] = _empty_signal_v2_group_entry()
            return
        summary[name] = {
            "signal_count": int(len(group)),
            "hit_5pct_rate": _rate_from_column_or_threshold(group, ("hit_5pct_before_drawdown_5pct", "hit_5pct_rate"), 5.0),
            "hit_10pct_rate": _rate_from_column_or_threshold(group, ("hit_10pct_before_drawdown_8pct", "hit_10pct_rate"), 10.0),
            "hit_10pct_before_drawdown_8pct_rate": _rate_from_column_or_threshold(
                group,
                ("hit_10pct_before_drawdown_8pct",),
                10.0,
            ),
            "avg_mfe_1h_pct": _average(group, "mfe_1h_pct"),
            "avg_mfe_4h_pct": _average(group, "mfe_4h_pct"),
            "avg_mfe_24h_pct": _average(group, "mfe_24h_pct"),
            "avg_mae_1h_pct": _average(group, "mae_1h_pct"),
            "avg_mae_4h_pct": _average(group, "mae_4h_pct"),
            "avg_mae_24h_pct": _average(group, "mae_24h_pct"),
            "median_time_to_hit_10pct_minutes": _median_minutes(group, "time_to_hit_10pct_minutes"),
        }

    grade_masks = {
        "continuation_A": signals["continuation_grade"].fillna("").astype(str).eq("A") if "continuation_grade" in signals.columns else pd.Series([False] * len(signals), index=signals.index),
        "continuation_B": signals["continuation_grade"].fillna("").astype(str).eq("B") if "continuation_grade" in signals.columns else pd.Series([False] * len(signals), index=signals.index),
        "ignition_A": signals["ignition_grade"].fillna("").astype(str).eq("A") if "ignition_grade" in signals.columns else pd.Series([False] * len(signals), index=signals.index),
        "ignition_B": signals["ignition_grade"].fillna("").astype(str).eq("B") if "ignition_grade" in signals.columns else pd.Series([False] * len(signals), index=signals.index),
        "ignition_EXTREME": signals["ignition_grade"].fillna("").astype(str).eq("EXTREME") if "ignition_grade" in signals.columns else pd.Series([False] * len(signals), index=signals.index),
    }
    for name, mask in grade_masks.items():
        _populate_group(name, mask)

    if "cross_exchange_confirmed" in signals.columns:
        confirmed = signals["cross_exchange_confirmed"].fillna(False).eq(True)
        _populate_group("cross_exchange_confirmed", confirmed)
        _populate_group("single_exchange_triggered", ~confirmed)
    if "chase_risk_score" in signals.columns:
        chase_risk = pd.to_numeric(signals["chase_risk_score"], errors="coerce")
        _populate_group("high_chase_risk", chase_risk >= 60)
        _populate_group("low_or_medium_chase_risk", chase_risk < 60)
    return summary


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
