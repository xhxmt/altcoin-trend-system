from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TradeCandidateRule:
    min_return_1h_pct: float = 6.0
    min_return_4h_pct: float = 10.0
    min_return_24h_pct: float = 12.0
    min_volume_ratio_24h: float = 5.0
    min_return_24h_percentile: float = 0.94
    min_return_7d_percentile: float = 0.84
    min_quality_score: float = 80.0


ITER25_RULE = TradeCandidateRule()


@dataclass(frozen=True)
class IgnitionCandidateRule:
    min_return_1h_pct: float = 8.0
    min_return_24h_pct: float = 25.0
    min_return_24h_percentile: float = 0.92
    min_relative_strength_score: float = 85.0
    min_quality_score: float = 80.0
    min_volume_ratio_24h: float = 1.8
    min_volume_breakout_score: float = 35.0
    min_derivatives_score: float = 30.0


IGNITION_RULE = IgnitionCandidateRule()


@dataclass(frozen=True)
class UltraHighConvictionRule:
    min_return_1h_pct: float = 12.0
    max_return_1h_pct: float = 35.0
    min_return_4h_pct: float = 38.0
    max_return_4h_pct: float = 110.0
    min_return_24h_pct: float = 50.0
    min_return_30d_pct: float = 65.0
    min_volume_ratio_24h: float = 5.0
    max_volume_ratio_24h: float = 10.0
    max_return_24h_rank: int = 3
    min_return_24h_percentile: float = 0.999
    min_return_7d_percentile: float = 0.988
    min_return_30d_percentile: float = 0.78
    min_quality_score: float = 80.0
    require_20d_breakout: bool = True


ULTRA_HIGH_CONVICTION_RULE = UltraHighConvictionRule()


def _get(row: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _float_value(row: Mapping[str, Any] | Any, key: str) -> float | None:
    value = _get(row, key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rank_or_percentile_gate(
    row: Mapping[str, Any] | Any,
    *,
    rank_key: str,
    max_rank: int | None,
    percentile_key: str,
    min_percentile: float,
) -> bool:
    rank = _float_value(row, rank_key)
    if rank is not None and max_rank is not None:
        return rank <= max_rank
    percentile = _float_value(row, percentile_key)
    return percentile is not None and percentile >= min_percentile


def _normalize_items(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        normalized = value.strip()
        return (normalized,) if normalized else ()
    if isinstance(value, Sequence):
        return tuple(str(item).strip() for item in value if str(item).strip())
    normalized = str(value).strip()
    return (normalized,) if normalized else ()


def is_continuation_candidate(row: Mapping[str, Any] | Any, rule: TradeCandidateRule = ITER25_RULE) -> bool:
    values = {
        "return_1h_pct": _float_value(row, "return_1h_pct"),
        "return_4h_pct": _float_value(row, "return_4h_pct"),
        "return_24h_pct": _float_value(row, "return_24h_pct"),
        "volume_ratio_24h": _float_value(row, "volume_ratio_24h"),
        "return_24h_percentile": _float_value(row, "return_24h_percentile"),
        "return_7d_percentile": _float_value(row, "return_7d_percentile"),
        "quality_score": _float_value(row, "quality_score"),
    }
    if any(value is None for value in values.values()):
        return False
    veto_reason_codes = _normalize_items(_get(row, "veto_reason_codes", None))
    return (
        values["return_1h_pct"] >= rule.min_return_1h_pct
        and values["return_4h_pct"] >= rule.min_return_4h_pct
        and values["return_24h_pct"] >= rule.min_return_24h_pct
        and values["volume_ratio_24h"] >= rule.min_volume_ratio_24h
        and values["return_24h_percentile"] >= rule.min_return_24h_percentile
        and values["return_7d_percentile"] >= rule.min_return_7d_percentile
        and values["quality_score"] >= rule.min_quality_score
        and not veto_reason_codes
    )


def is_ignition_candidate(row: Mapping[str, Any] | Any, rule: IgnitionCandidateRule = IGNITION_RULE) -> bool:
    values = {
        "return_1h_pct": _float_value(row, "return_1h_pct"),
        "return_24h_pct": _float_value(row, "return_24h_pct"),
        "return_24h_percentile": _float_value(row, "return_24h_percentile"),
        "relative_strength_score": _float_value(row, "relative_strength_score"),
        "quality_score": _float_value(row, "quality_score"),
        "volume_ratio_24h": _float_value(row, "volume_ratio_24h"),
        "volume_breakout_score": _float_value(row, "volume_breakout_score"),
        "derivatives_score": _float_value(row, "derivatives_score"),
    }
    if any(value is None for value in values.values()):
        return False
    veto_reason_codes = _normalize_items(_get(row, "veto_reason_codes", None))
    volume_confirmed = (
        values["volume_ratio_24h"] >= rule.min_volume_ratio_24h
        or values["volume_breakout_score"] >= rule.min_volume_breakout_score
    )
    return (
        values["return_1h_pct"] >= rule.min_return_1h_pct
        and values["return_24h_pct"] >= rule.min_return_24h_pct
        and values["return_24h_percentile"] >= rule.min_return_24h_percentile
        and values["relative_strength_score"] >= rule.min_relative_strength_score
        and values["quality_score"] >= rule.min_quality_score
        and volume_confirmed
        and values["derivatives_score"] >= rule.min_derivatives_score
        and not veto_reason_codes
    )


def is_ultra_high_conviction_candidate(
    row: Mapping[str, Any] | Any,
    rule: UltraHighConvictionRule = ULTRA_HIGH_CONVICTION_RULE,
) -> bool:
    values = {
        "return_1h_pct": _float_value(row, "return_1h_pct"),
        "return_4h_pct": _float_value(row, "return_4h_pct"),
        "return_24h_pct": _float_value(row, "return_24h_pct"),
        "return_30d_pct": _float_value(row, "return_30d_pct"),
        "volume_ratio_24h": _float_value(row, "volume_ratio_24h"),
        "return_7d_percentile": _float_value(row, "return_7d_percentile"),
        "return_30d_percentile": _float_value(row, "return_30d_percentile"),
        "quality_score": _float_value(row, "quality_score"),
    }
    if any(value is None for value in values.values()):
        return False
    veto_reason_codes = _normalize_items(_get(row, "veto_reason_codes", None))
    if veto_reason_codes:
        return False
    breakout_20d = bool(_get(row, "breakout_20d", False))
    if rule.require_20d_breakout and not breakout_20d:
        return False

    return (
        values["return_1h_pct"] >= rule.min_return_1h_pct
        and values["return_1h_pct"] <= rule.max_return_1h_pct
        and values["return_4h_pct"] >= rule.min_return_4h_pct
        and values["return_4h_pct"] <= rule.max_return_4h_pct
        and values["return_24h_pct"] >= rule.min_return_24h_pct
        and values["return_30d_pct"] >= rule.min_return_30d_pct
        and values["volume_ratio_24h"] >= rule.min_volume_ratio_24h
        and values["volume_ratio_24h"] <= rule.max_volume_ratio_24h
        and _rank_or_percentile_gate(
            row,
            rank_key="return_24h_rank",
            max_rank=rule.max_return_24h_rank,
            percentile_key="return_24h_percentile",
            min_percentile=rule.min_return_24h_percentile,
        )
        and values["return_7d_percentile"] >= rule.min_return_7d_percentile
        and values["return_30d_percentile"] >= rule.min_return_30d_percentile
        and values["quality_score"] >= rule.min_quality_score
    )


def is_trade_candidate(row: Mapping[str, Any] | Any, rule: TradeCandidateRule = ITER25_RULE) -> bool:
    return is_continuation_candidate(row, rule=rule)
