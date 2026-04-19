from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


def clamp_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


@dataclass(frozen=True)
class DerivativesFeature:
    oi_delta_1h: float | None
    oi_delta_4h: float | None
    funding_zscore: float | None
    taker_buy_sell_ratio: float | None
    derivatives_score: float


def _latest_non_null(ordered: pd.DataFrame, column: str) -> tuple[pd.Timestamp, float] | None:
    if column not in ordered.columns:
        return None
    values = ordered[["ts", column]].dropna()
    if values.empty:
        return None
    latest = values.iloc[-1]
    return latest["ts"], float(latest[column])


def _value_at_or_before(ordered: pd.DataFrame, column: str, ts: pd.Timestamp) -> float | None:
    if column not in ordered.columns:
        return None
    values = ordered[ordered["ts"] <= ts][column].dropna()
    if values.empty:
        return None
    value = float(values.iloc[-1])
    return value if value > 0 else None


def _delta_pct(ordered: pd.DataFrame, column: str, hours: int) -> float | None:
    latest = _latest_non_null(ordered, column)
    if latest is None:
        return None
    latest_ts, latest_value = latest
    previous = _value_at_or_before(ordered, column, latest_ts - pd.Timedelta(hours=hours))
    if previous is None:
        return None
    return ((latest_value / previous) - 1.0) * 100.0


def _funding_zscore(ordered: pd.DataFrame) -> float | None:
    if "funding_rate" not in ordered.columns:
        return None
    values = ordered["funding_rate"].dropna().astype(float)
    if len(values) < 2:
        return None
    std = float(values.std(ddof=0))
    if std == 0:
        return 0.0
    return (float(values.iloc[-1]) - float(values.mean())) / std


def _taker_buy_sell_ratio(ordered: pd.DataFrame) -> float | None:
    if "taker_buy_quote" not in ordered.columns or "quote_volume" not in ordered.columns:
        return None
    latest = ordered.dropna(subset=["taker_buy_quote", "quote_volume"])
    if latest.empty:
        return None
    row = latest.iloc[-1]
    buy_quote = float(row["taker_buy_quote"])
    total_quote = float(row["quote_volume"])
    sell_quote = total_quote - buy_quote
    if sell_quote <= 0:
        return None
    return buy_quote / sell_quote


def compute_derivatives_features(frame: pd.DataFrame) -> DerivativesFeature:
    if frame.empty:
        return DerivativesFeature(None, None, None, None, 50.0)

    ordered = frame.copy()
    ordered["ts"] = pd.to_datetime(ordered["ts"], utc=True)
    ordered = ordered.sort_values("ts")
    oi_delta_1h = _delta_pct(ordered, "open_interest", 1)
    oi_delta_4h = _delta_pct(ordered, "open_interest", 4)
    funding_zscore = _funding_zscore(ordered)
    taker_ratio = _taker_buy_sell_ratio(ordered)

    first_close = float(ordered["close"].iloc[0]) if "close" in ordered.columns else 0.0
    latest_close = float(ordered["close"].iloc[-1]) if "close" in ordered.columns else 0.0
    price_return = ((latest_close / first_close) - 1.0) * 100.0 if first_close > 0 else 0.0

    score = 50.0
    for delta in (oi_delta_1h, oi_delta_4h):
        if delta is None:
            continue
        if price_return > 0 and delta > 0:
            score += min(12.0, delta * 0.8)
        elif price_return > 0 and delta < 0:
            score += max(-15.0, delta * 0.9)
        elif price_return < 0 and delta > 0:
            score -= min(10.0, delta * 0.5)

    if funding_zscore is not None and funding_zscore > 1.0:
        score -= min(18.0, (funding_zscore - 1.0) * 8.0)
    elif funding_zscore is not None and funding_zscore < -1.0 and price_return > 0:
        score += min(6.0, abs(funding_zscore + 1.0) * 3.0)

    if taker_ratio is not None:
        if 1.05 <= taker_ratio <= 1.8:
            score += 5.0
        elif taker_ratio > 3.0:
            score -= 8.0

    return DerivativesFeature(
        oi_delta_1h=oi_delta_1h,
        oi_delta_4h=oi_delta_4h,
        funding_zscore=funding_zscore,
        taker_buy_sell_ratio=taker_ratio,
        derivatives_score=clamp_score(score),
    )
