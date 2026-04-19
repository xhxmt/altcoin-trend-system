from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


def clamp_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


@dataclass(frozen=True)
class RelativeStrengthFeature:
    return_7d: float | None
    return_30d: float | None
    rs_btc_7d: float | None
    rs_eth_7d: float | None
    rs_btc_30d: float | None
    rs_eth_30d: float | None
    relative_strength_score: float


def _clean_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _return_pct(group: pd.DataFrame, days: int) -> float | None:
    if group.empty:
        return None

    ordered = group.sort_values("ts")
    latest = ordered.iloc[-1]
    latest_close = float(latest["close"])
    anchor_ts = latest["ts"] - pd.Timedelta(days=days)
    history = ordered[ordered["ts"] <= anchor_ts]
    if history.empty:
        return None

    anchor_close = float(history.iloc[-1]["close"])
    if anchor_close <= 0:
        return None
    return round(((latest_close / anchor_close) - 1.0) * 100.0, 6)


def _score_from_edges(edges: list[float]) -> float:
    if not edges:
        return 50.0
    weighted_edge = sum(edges) / len(edges)
    return clamp_score(50.0 + weighted_edge * 3.0)


def _fallback_score(
    return_7d: float | None,
    return_30d: float | None,
    median_7d: float | None,
    median_30d: float | None,
) -> float:
    edges: list[float] = []
    if return_7d is not None and median_7d is not None:
        edges.extend([return_7d - median_7d, return_7d - median_7d])
    if return_30d is not None and median_30d is not None:
        edges.append(return_30d - median_30d)
    return _score_from_edges(edges)


def _benchmark_score(
    return_7d: float | None,
    return_30d: float | None,
    btc_7d: float | None,
    eth_7d: float | None,
    btc_30d: float | None,
    eth_30d: float | None,
) -> tuple[float | None, float | None, float | None, float | None, float]:
    rs_btc_7d = return_7d - btc_7d if return_7d is not None and btc_7d is not None else None
    rs_eth_7d = return_7d - eth_7d if return_7d is not None and eth_7d is not None else None
    rs_btc_30d = return_30d - btc_30d if return_30d is not None and btc_30d is not None else None
    rs_eth_30d = return_30d - eth_30d if return_30d is not None and eth_30d is not None else None

    edges: list[float] = []
    for value in (rs_btc_7d, rs_eth_7d):
        if value is not None:
            edges.extend([value, value])
    for value in (rs_btc_30d, rs_eth_30d):
        if value is not None:
            edges.append(value)
    return rs_btc_7d, rs_eth_7d, rs_btc_30d, rs_eth_30d, _score_from_edges(edges)


def build_relative_strength_features(frame: pd.DataFrame) -> dict[int, RelativeStrengthFeature]:
    if frame.empty:
        return {}

    working = frame.copy()
    working["ts"] = pd.to_datetime(working["ts"], utc=True)
    returns: dict[int, dict[str, Any]] = {}
    for asset_id, group in working.groupby("asset_id"):
        latest = group.sort_values("ts").iloc[-1]
        returns[int(asset_id)] = {
            "exchange": str(latest["exchange"]),
            "symbol": str(latest["symbol"]).upper(),
            "return_7d": _return_pct(group, 7),
            "return_30d": _return_pct(group, 30),
        }

    result: dict[int, RelativeStrengthFeature] = {}
    for _, exchange_rows in pd.DataFrame.from_dict(returns, orient="index").groupby("exchange"):
        benchmark_by_symbol = {str(row["symbol"]): row for row in exchange_rows.to_dict("records")}
        btc = benchmark_by_symbol.get("BTCUSDT", {})
        eth = benchmark_by_symbol.get("ETHUSDT", {})
        btc_7d = _clean_float(btc.get("return_7d")) if btc else None
        eth_7d = _clean_float(eth.get("return_7d")) if eth else None
        btc_30d = _clean_float(btc.get("return_30d")) if btc else None
        eth_30d = _clean_float(eth.get("return_30d")) if eth else None
        median_7d = exchange_rows["return_7d"].dropna().median()
        median_30d = exchange_rows["return_30d"].dropna().median()
        median_7d_value = float(median_7d) if pd.notna(median_7d) else None
        median_30d_value = float(median_30d) if pd.notna(median_30d) else None

        for raw_asset_id, row in exchange_rows.iterrows():
            asset_id = int(raw_asset_id)
            return_7d = _clean_float(row["return_7d"])
            return_30d = _clean_float(row["return_30d"])
            if any(value is not None for value in (btc_7d, eth_7d, btc_30d, eth_30d)):
                rs_btc_7d, rs_eth_7d, rs_btc_30d, rs_eth_30d, score = _benchmark_score(
                    return_7d,
                    return_30d,
                    btc_7d,
                    eth_7d,
                    btc_30d,
                    eth_30d,
                )
            else:
                rs_btc_7d = rs_eth_7d = rs_btc_30d = rs_eth_30d = None
                score = _fallback_score(return_7d, return_30d, median_7d_value, median_30d_value)

            result[asset_id] = RelativeStrengthFeature(
                return_7d=return_7d,
                return_30d=return_30d,
                rs_btc_7d=rs_btc_7d,
                rs_eth_7d=rs_eth_7d,
                rs_btc_30d=rs_btc_30d,
                rs_eth_30d=rs_eth_30d,
                relative_strength_score=score,
            )

    return result
