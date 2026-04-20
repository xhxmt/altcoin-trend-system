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


def is_trade_candidate(row: Mapping[str, Any] | Any, rule: TradeCandidateRule = ITER25_RULE) -> bool:
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
