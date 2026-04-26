from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import Engine, text

from altcoin_trend.features.resample import resample_market_1m
from altcoin_trend.signals.trade_candidate import is_trade_candidate
from altcoin_trend.signals.v2 import compute_volume_impulse_score, evaluate_signal_v2


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
        "reacceleration_A": _empty_signal_v2_group_entry(),
        "reacceleration_B": _empty_signal_v2_group_entry(),
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
        "mfe_before_dd8_pct": 0.0,
        "mae_before_hit_10pct": 0.0,
        "mae_after_hit_10pct": None,
        "hit_5pct_before_drawdown_5pct": False,
        "hit_10pct_before_drawdown_8pct": False,
        "hit_10pct_first": None,
        "drawdown_8pct_first": None,
        "time_to_hit_5pct_minutes": None,
        "time_to_hit_10pct_minutes": None,
        "time_to_drawdown_8pct_minutes": None,
    }


def _summarize_path_mfe_pct(close: float, rows: pd.DataFrame) -> float:
    if rows.empty:
        return 0.0
    window_high = max(float(rows["high"].max()), close)
    return round(max((window_high / close - 1.0) * 100.0, 0.0), 6)


def _summarize_path_mae_pct(close: float, rows: pd.DataFrame) -> float:
    if rows.empty:
        return 0.0
    window_low = min(float(rows["low"].min()), close)
    return round(max((1.0 - window_low / close) * 100.0, 0.0), 6)


def _first_barrier_index(
    rows: pd.DataFrame,
    *,
    column: str,
    comparison: str,
    threshold: float,
) -> int | None:
    if rows.empty:
        return None
    if comparison == "ge":
        matched = rows.index[rows[column] >= threshold]
    elif comparison == "le":
        matched = rows.index[rows[column] <= threshold]
    else:
        raise ValueError(f"unsupported comparison: {comparison}")
    return int(matched[0]) if len(matched) else None


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
    future["ts"] = pd.to_datetime(future["ts"], utc=True, errors="coerce", format="mixed")
    future["high"] = pd.to_numeric(future["high"], errors="coerce")
    future["low"] = pd.to_numeric(future["low"], errors="coerce")
    future = future.dropna(subset=["ts", "high", "low"])
    future = future[future["ts"] > signal_ts_utc].sort_values("ts").reset_index(drop=True)
    if future.empty:
        return labels

    windows = {
        "1h": signal_ts_utc + pd.Timedelta(hours=1),
        "4h": signal_ts_utc + pd.Timedelta(hours=4),
        "24h": signal_ts_utc + pd.Timedelta(hours=24),
    }
    horizon_24h = windows["24h"]

    for window_name, window_end in windows.items():
        window_rows = future[future["ts"] <= window_end]
        if window_rows.empty:
            continue
        window_high = max(float(window_rows["high"].max()), close)
        window_low = min(float(window_rows["low"].min()), close)
        labels[f"mfe_{window_name}_pct"] = round(max((window_high / close - 1.0) * 100.0, 0.0), 6)
        labels[f"mae_{window_name}_pct"] = round(max((1.0 - window_low / close) * 100.0, 0.0), 6)

    for target_pct, drawdown_pct, hit_key, time_key in (
        (0.05, 0.05, "hit_5pct_before_drawdown_5pct", "time_to_hit_5pct_minutes"),
        (0.10, 0.08, "hit_10pct_before_drawdown_8pct", "time_to_hit_10pct_minutes"),
    ):
        target_price = round(close * (1.0 + target_pct), 12)
        drawdown_price = round(close * (1.0 - drawdown_pct), 12)
        for row in future[future["ts"] <= horizon_24h].itertuples(index=False):
            row_high = float(row.high)
            row_low = float(row.low)
            if row_low <= drawdown_price:
                labels[hit_key] = False
                labels[time_key] = None
                break
            if row_high >= target_price:
                hit_time = _coerce_utc_timestamp(row.ts)
                labels[hit_key] = True
                labels[time_key] = round((hit_time - signal_ts_utc).total_seconds() / 60.0, 6)
                break
        else:
            labels[hit_key] = False
            labels[time_key] = None

    horizon_rows = future[future["ts"] <= horizon_24h].reset_index(drop=True)
    if horizon_rows.empty:
        return labels

    target_10pct_price = round(close * 1.10, 12)
    drawdown_8pct_price = round(close * 0.92, 12)
    first_hit_10pct_idx = _first_barrier_index(
        horizon_rows,
        column="high",
        comparison="ge",
        threshold=target_10pct_price,
    )
    first_drawdown_8pct_idx = _first_barrier_index(
        horizon_rows,
        column="low",
        comparison="le",
        threshold=drawdown_8pct_price,
    )

    if first_drawdown_8pct_idx is not None:
        drawdown_time = _coerce_utc_timestamp(horizon_rows.iloc[first_drawdown_8pct_idx]["ts"])
        labels["time_to_drawdown_8pct_minutes"] = round(
            (drawdown_time - signal_ts_utc).total_seconds() / 60.0,
            6,
        )

    if first_hit_10pct_idx is None and first_drawdown_8pct_idx is None:
        labels["hit_10pct_first"] = None
        labels["drawdown_8pct_first"] = None
    elif first_hit_10pct_idx is not None and (
        first_drawdown_8pct_idx is None or first_hit_10pct_idx < first_drawdown_8pct_idx
    ):
        labels["hit_10pct_first"] = True
        labels["drawdown_8pct_first"] = False
    else:
        labels["hit_10pct_first"] = False
        labels["drawdown_8pct_first"] = True

    if first_drawdown_8pct_idx is None:
        rows_before_drawdown = horizon_rows
    else:
        rows_before_drawdown = horizon_rows.iloc[:first_drawdown_8pct_idx]
    labels["mfe_before_dd8_pct"] = _summarize_path_mfe_pct(close, rows_before_drawdown)

    if first_hit_10pct_idx is None:
        rows_before_hit = horizon_rows
    else:
        rows_before_hit = horizon_rows.iloc[: first_hit_10pct_idx + 1]
    labels["mae_before_hit_10pct"] = _summarize_path_mae_pct(close, rows_before_hit)

    if first_hit_10pct_idx is None:
        labels["mae_after_hit_10pct"] = None
    else:
        rows_after_hit = horizon_rows.iloc[first_hit_10pct_idx + 1 :]
        labels["mae_after_hit_10pct"] = _summarize_path_mae_pct(close, rows_after_hit)

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

    def _rate_from_explicit_column(group: pd.DataFrame, column: str) -> float:
        if column not in group.columns:
            return 0.0
        series = _as_numeric(group, column).fillna(0.0)
        if series.empty:
            return 0.0
        return round(float((series > 0).mean() * 100.0), 6)

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
            "hit_5pct_rate": _rate_from_column_or_threshold(group, ("hit_5pct_rate",), 5.0),
            "hit_10pct_rate": _rate_from_column_or_threshold(group, ("hit_10pct_rate",), 10.0),
            "hit_10pct_before_drawdown_8pct_rate": _rate_from_explicit_column(group, "hit_10pct_before_drawdown_8pct"),
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
        "reacceleration_A": signals["reacceleration_grade"].fillna("").astype(str).eq("A") if "reacceleration_grade" in signals.columns else pd.Series([False] * len(signals), index=signals.index),
        "reacceleration_B": signals["reacceleration_grade"].fillna("").astype(str).eq("B") if "reacceleration_grade" in signals.columns else pd.Series([False] * len(signals), index=signals.index),
    }
    for name, mask in grade_masks.items():
        _populate_group(name, mask)

    if "ultra_high_conviction" in signals.columns:
        ultra_high_conviction = signals["ultra_high_conviction"].fillna(False).eq(True)
        _populate_group("ultra_high_conviction", ultra_high_conviction)

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

    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["high"] = pd.to_numeric(frame["high"], errors="coerce")
    frame["low"] = pd.to_numeric(frame["low"], errors="coerce")
    frame["quote_volume"] = pd.to_numeric(frame["quote_volume"], errors="coerce")

    frame["return_1h_pct"] = grouped["close"].pct_change(1) * 100.0
    frame["return_4h_pct"] = grouped["close"].pct_change(4) * 100.0
    frame["return_24h_pct"] = grouped["close"].pct_change(24) * 100.0
    frame["return_7d_pct"] = grouped["close"].pct_change(24 * 7) * 100.0
    frame["return_30d_pct"] = grouped["close"].pct_change(24 * 30) * 100.0
    rolling_volume_4h = grouped["quote_volume"].rolling(4, min_periods=4).mean().reset_index(level=0, drop=True)
    rolling_volume = grouped["quote_volume"].rolling(24, min_periods=12).mean().reset_index(level=0, drop=True)
    frame["volume_ratio_4h"] = frame["quote_volume"] / rolling_volume_4h
    frame["volume_ratio_24h"] = frame["quote_volume"] / rolling_volume
    frame["volume_ratio_1h"] = frame["volume_ratio_24h"]
    prior_high_20d = grouped["high"].transform(lambda series: series.shift(1).rolling(24 * 20, min_periods=24 * 20).max())
    frame["breakout_20d"] = (frame["close"] > prior_high_20d).fillna(False)
    frame["future_high_1h"] = grouped["high"].shift(-1)
    frame["future_max_return_1h"] = (frame["future_high_1h"] / frame["close"]) - 1.0
    frame["quality_score"] = 100.0
    frame["veto_reason_codes"] = [[] for _ in range(len(frame))]
    frame["return_24h_rank"] = frame.groupby(["exchange", "ts"])["return_24h_pct"].rank(ascending=False, method="min")
    frame["return_7d_rank"] = frame.groupby(["exchange", "ts"])["return_7d_pct"].rank(ascending=False, method="min")
    frame["return_30d_rank"] = frame.groupby(["exchange", "ts"])["return_30d_pct"].rank(ascending=False, method="min")
    return_24h_count = frame.groupby(["exchange", "ts"])["return_24h_pct"].transform("count")
    return_7d_count = frame.groupby(["exchange", "ts"])["return_7d_pct"].transform("count")
    return_30d_count = frame.groupby(["exchange", "ts"])["return_30d_pct"].transform("count")
    frame["return_24h_percentile"] = pd.Series(index=frame.index, dtype="float64")
    frame["return_7d_percentile"] = pd.Series(index=frame.index, dtype="float64")
    frame["return_30d_percentile"] = pd.Series(index=frame.index, dtype="float64")
    multi_24h = return_24h_count > 1
    multi_7d = return_7d_count > 1
    multi_30d = return_30d_count > 1
    frame.loc[multi_24h, "return_24h_percentile"] = 1.0 - ((frame.loc[multi_24h, "return_24h_rank"] - 1.0) / (return_24h_count[multi_24h] - 1.0))
    frame.loc[multi_7d, "return_7d_percentile"] = 1.0 - ((frame.loc[multi_7d, "return_7d_rank"] - 1.0) / (return_7d_count[multi_7d] - 1.0))
    frame.loc[multi_30d, "return_30d_percentile"] = 1.0 - ((frame.loc[multi_30d, "return_30d_rank"] - 1.0) / (return_30d_count[multi_30d] - 1.0))
    single_24h = (return_24h_count == 1) & frame["return_24h_pct"].notna()
    single_7d = (return_7d_count == 1) & frame["return_7d_pct"].notna()
    single_30d = (return_30d_count == 1) & frame["return_30d_pct"].notna()
    frame.loc[single_24h, "return_24h_percentile"] = 1.0
    frame.loc[single_7d, "return_7d_percentile"] = 1.0
    frame.loc[single_30d, "return_30d_percentile"] = 1.0
    frame["relative_strength_score"] = frame["return_24h_percentile"].mul(100.0).fillna(50.0)
    frame["derivatives_score"] = 50.0
    frame["volume_impulse_score"] = frame.apply(compute_volume_impulse_score, axis=1)
    frame["volume_breakout_score"] = frame["volume_impulse_score"]
    signal_v2 = frame.apply(evaluate_signal_v2, axis=1)
    frame["continuation_grade"] = [result.continuation_grade for result in signal_v2]
    frame["ignition_grade"] = [result.ignition_grade for result in signal_v2]
    frame["reacceleration_grade"] = [result.reacceleration_grade for result in signal_v2]
    frame["ultra_high_conviction"] = [result.ultra_high_conviction for result in signal_v2]
    frame["signal_priority"] = [result.signal_priority for result in signal_v2]
    frame["chase_risk_score"] = [result.chase_risk_score for result in signal_v2]
    frame["actionability_score"] = [result.actionability_score for result in signal_v2]
    frame["risk_flags"] = [result.risk_flags for result in signal_v2]
    frame["cross_exchange_confirmed"] = False
    frame["mfe_1h_pct"] = frame["future_max_return_1h"].fillna(0.0) * 100.0
    frame["mfe_4h_pct"] = frame["mfe_1h_pct"]
    frame["mfe_24h_pct"] = frame["mfe_1h_pct"]
    frame["mae_1h_pct"] = 0.0
    frame["mae_4h_pct"] = 0.0
    frame["mae_24h_pct"] = 0.0
    frame["hit_5pct_before_drawdown_5pct"] = frame["future_max_return_1h"] >= 0.05
    frame["hit_10pct_before_drawdown_8pct"] = frame["future_max_return_1h"] >= 0.10
    frame["time_to_hit_5pct_minutes"] = None
    frame["time_to_hit_10pct_minutes"] = None
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


def run_signal_v2_backtest(
    engine: Engine,
    exchange: str,
    start: datetime,
    end: datetime,
) -> dict[str, dict[str, float | int]]:
    start_utc = _coerce_utc_datetime(start)
    end_utc = _coerce_utc_datetime(end)
    if start_utc >= end_utc:
        raise ValueError("start must be earlier than end")

    market_start = start_utc - timedelta(days=31)
    market_end = end_utc + timedelta(hours=25)
    market_rows = _fetch_market_rows(engine, exchange=exchange, start=market_start, end=market_end)
    if market_rows.empty:
        return summarize_signal_v2_groups(pd.DataFrame())

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
        return summarize_signal_v2_groups(pd.DataFrame())

    features = _prepare_feature_frame(pd.concat(bars, ignore_index=True))
    window = features[(features["ts"] >= start_utc) & (features["ts"] < end_utc)].copy()
    if window.empty:
        return summarize_signal_v2_groups(pd.DataFrame())

    feature_groups = {asset_id: group.copy() for asset_id, group in features.groupby("asset_id", sort=False)}
    for idx, row in window.iterrows():
        asset_features = feature_groups.get(row["asset_id"])
        if asset_features is None:
            continue
        labels = compute_forward_path_labels(row["ts"], row["close"], asset_features[["ts", "high", "low"]])
        for key, value in labels.items():
            window.at[idx, key] = value

    def _has_value(value: Any) -> bool:
        if pd.isna(value):
            return False
        return str(value).strip() != ""

    signal_priority = pd.to_numeric(window["signal_priority"], errors="coerce") if "signal_priority" in window.columns else pd.Series(0.0, index=window.index)
    continuation_signal = window["continuation_grade"].map(_has_value) if "continuation_grade" in window.columns else pd.Series(False, index=window.index)
    ignition_signal = window["ignition_grade"].map(_has_value) if "ignition_grade" in window.columns else pd.Series(False, index=window.index)
    signal_mask = (signal_priority.fillna(0.0) > 0) | continuation_signal.fillna(False) | ignition_signal.fillna(False)
    window = window[signal_mask].copy()
    if window.empty:
        return summarize_signal_v2_groups(pd.DataFrame())

    return summarize_signal_v2_groups(window)
