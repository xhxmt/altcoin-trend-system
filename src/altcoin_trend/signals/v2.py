from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from altcoin_trend.signals.trade_candidate import is_ultra_high_conviction_candidate


CONTINUATION_A = "A"
CONTINUATION_B = "B"
IGNITION_EXTREME = "EXTREME"
IGNITION_A = "A"
IGNITION_B = "B"
ULTRA_HIGH_CONVICTION_FLAG = "ULTRA_HIGH_CONVICTION"

_CONTINUATION_PRIORITY = {CONTINUATION_A: 3, CONTINUATION_B: 2}
_IGNITION_PRIORITY = {IGNITION_EXTREME: 3, IGNITION_A: 2, IGNITION_B: 1}


@dataclass(frozen=True)
class SignalV2Result:
    continuation_grade: str | None
    ignition_grade: str | None
    ultra_high_conviction: bool
    signal_priority: int
    risk_flags: tuple[str, ...]
    chase_risk_score: float
    actionability_score: float


def get_value(row: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def float_value(row: Mapping[str, Any] | Any, key: str) -> float | None:
    value = get_value(row, key)
    if value is None:
        return None
    try:
        numeric_value = float(value)
        if math.isnan(numeric_value):
            return None
        return numeric_value
    except (TypeError, ValueError):
        return None


def normalize_items(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        normalized = value.strip()
        return (normalized,) if normalized else ()
    if isinstance(value, Sequence):
        items = tuple(str(item).strip() for item in value if item is not None and str(item).strip())
        return items
    normalized = str(value).strip()
    return (normalized,) if normalized else ()


def has_veto(row: Mapping[str, Any] | Any) -> bool:
    return bool(normalize_items(get_value(row, "veto_reason_codes")))


def ratio_score(value: float | None, full_at: float = 5.0) -> float:
    if value is None or value <= 1.0 or full_at <= 1.0:
        return 0.0
    if value >= full_at:
        return 100.0
    score = math.log(value) / math.log(full_at) * 100.0
    return round(max(0.0, min(100.0, score)), 4)


def _bool_value(row: Mapping[str, Any] | Any, key: str) -> bool:
    value = get_value(row, key)
    if isinstance(value, bool):
        return value
    return bool(value)


def compute_volume_impulse_score(row: Mapping[str, Any] | Any) -> float:
    volume_ratio_1h = float_value(row, "volume_ratio_1h")
    s1h = ratio_score(volume_ratio_1h if volume_ratio_1h is not None else 1.0, full_at=6.0)
    s4h = ratio_score(float_value(row, "volume_ratio_4h"), full_at=5.0)
    s24h = ratio_score(float_value(row, "volume_ratio_24h"), full_at=4.0)
    score = 0.40 * s1h + 0.35 * s4h + 0.25 * s24h
    breakout = _bool_value(row, "breakout_20d")
    if breakout:
        score += 10.0
    return round(min(100.0, score), 4)


def is_top_24h(row: Mapping[str, Any] | Any, *, max_rank: int = 3, min_percentile: float = 0.94) -> bool:
    rank = float_value(row, "return_24h_rank")
    percentile = float_value(row, "return_24h_percentile")
    return (rank is not None and rank <= max_rank) or (percentile is not None and percentile >= min_percentile)


def is_top_7d(row: Mapping[str, Any] | Any, *, max_rank: int = 5, min_percentile: float = 0.84) -> bool:
    rank = float_value(row, "return_7d_rank")
    percentile = float_value(row, "return_7d_percentile")
    return (rank is not None and rank <= max_rank) or (percentile is not None and percentile >= min_percentile)


def _confirm_volume(row: Mapping[str, Any] | Any, *, ratio_min: float, impulse_min: float, breakout_min: float) -> bool:
    volume_ratio_24h = float_value(row, "volume_ratio_24h") or 0.0
    volume_impulse_score = float_value(row, "volume_impulse_score")
    volume_breakout_score = float_value(row, "volume_breakout_score") or 0.0
    return (
        volume_ratio_24h >= ratio_min
        or (volume_impulse_score is not None and volume_impulse_score >= impulse_min)
        or volume_breakout_score >= breakout_min
    )


def continuation_grade(row: Mapping[str, Any] | Any) -> str | None:
    if has_veto(row):
        return None

    return_1h_pct = float_value(row, "return_1h_pct")
    return_4h_pct = float_value(row, "return_4h_pct")
    return_24h_pct = float_value(row, "return_24h_pct")
    quality_score = float_value(row, "quality_score")
    if any(value is None for value in (return_1h_pct, return_4h_pct, return_24h_pct, quality_score)):
        return None
    if not (
        return_1h_pct >= 6.0
        and return_4h_pct >= 10.0
        and return_24h_pct >= 12.0
        and (float_value(row, "volume_ratio_24h") or 0.0) >= 5.0
        and is_top_24h(row, max_rank=3, min_percentile=0.94)
        and is_top_7d(row, max_rank=5, min_percentile=0.84)
        and quality_score >= 80.0
    ):
        return None

    relative_strength_score = float_value(row, "relative_strength_score") or 0.0
    derivatives_score = float_value(row, "derivatives_score") or 0.0
    if (
        relative_strength_score >= 85.0
        and derivatives_score >= 45.0
        and (
            (float_value(row, "volume_breakout_score") or 0.0) >= 50.0
            or (float_value(row, "volume_impulse_score") or 0.0) >= 50.0
        )
    ):
        return CONTINUATION_A
    return CONTINUATION_B


def ignition_grade(row: Mapping[str, Any] | Any) -> str | None:
    if has_veto(row):
        return None

    return_1h_pct = float_value(row, "return_1h_pct")
    return_24h_pct = float_value(row, "return_24h_pct")
    relative_strength_score = float_value(row, "relative_strength_score")
    quality_score = float_value(row, "quality_score")
    derivatives_score = float_value(row, "derivatives_score")
    if any(
        value is None
        for value in (
            return_1h_pct,
            return_24h_pct,
            relative_strength_score,
            quality_score,
            derivatives_score,
        )
    ):
        return None

    top_24h = is_top_24h(row, max_rank=3, min_percentile=0.94)
    if (
        return_1h_pct >= 20.0
        and return_24h_pct >= 70.0
        and top_24h
        and relative_strength_score >= 90.0
        and quality_score >= 80.0
        and _confirm_volume(row, ratio_min=1.5, impulse_min=35.0, breakout_min=35.0)
        and derivatives_score >= 25.0
    ):
        return IGNITION_EXTREME

    if (
        return_1h_pct >= 10.0
        and return_24h_pct >= 35.0
        and top_24h
        and relative_strength_score >= 90.0
        and quality_score >= 85.0
        and _confirm_volume(row, ratio_min=2.2, impulse_min=45.0, breakout_min=45.0)
        and derivatives_score >= 35.0
    ):
        return IGNITION_A

    if (
        return_1h_pct >= 8.0
        and return_24h_pct >= 25.0
        and is_top_24h(row, max_rank=3, min_percentile=0.92)
        and relative_strength_score >= 85.0
        and quality_score >= 80.0
        and _confirm_volume(row, ratio_min=1.8, impulse_min=45.0, breakout_min=35.0)
        and derivatives_score >= 30.0
    ):
        return IGNITION_B

    return None


_continuation_grade_fn = continuation_grade
_ignition_grade_fn = ignition_grade


def signal_priority_for(
    continuation_grade_value: str | None,
    ignition_grade_value: str | None,
) -> int:
    priorities = []
    if continuation_grade_value in _CONTINUATION_PRIORITY:
        priorities.append(_CONTINUATION_PRIORITY[continuation_grade_value])
    if ignition_grade_value in _IGNITION_PRIORITY:
        priorities.append(_IGNITION_PRIORITY[ignition_grade_value])
    return max(priorities) if priorities else 0


def compute_chase_risk_score(row: Mapping[str, Any] | Any) -> float:
    score = 0.0
    return_1h_pct = float_value(row, "return_1h_pct") or 0.0
    return_24h_pct = float_value(row, "return_24h_pct") or 0.0
    funding_zscore = float_value(row, "funding_zscore") or 0.0
    taker_buy_sell_ratio = float_value(row, "taker_buy_sell_ratio") or 0.0

    if return_1h_pct >= 15.0:
        score += 20.0
    if return_1h_pct >= 25.0:
        score += 20.0
    if return_24h_pct >= 60.0:
        score += 20.0
    if return_24h_pct >= 100.0:
        score += 20.0
    if funding_zscore >= 2.0:
        score += 10.0
    if taker_buy_sell_ratio >= 2.2:
        score += 10.0
    return round(min(100.0, score), 4)


def compute_risk_flags(
    row: Mapping[str, Any] | Any,
    *,
    ignition_grade: str | None = None,
    chase_risk_score: float | None = None,
    ultra_high_conviction: bool | None = None,
) -> tuple[str, ...]:
    if chase_risk_score is None:
        chase_risk_score = compute_chase_risk_score(row)
    if ultra_high_conviction is None:
        ultra_high_conviction = is_ultra_high_conviction_candidate(row)

    flags: list[str] = []
    if ultra_high_conviction:
        flags.append(ULTRA_HIGH_CONVICTION_FLAG)
    if ignition_grade == IGNITION_EXTREME:
        flags.append("EXTREME_MOVE")
    if chase_risk_score >= 60.0:
        flags.append("CHASE_RISK")
    if (float_value(row, "funding_zscore") or 0.0) >= 2.5:
        flags.append("FUNDING_OVERHEAT")
    if (float_value(row, "oi_delta_1h") or 0.0) < 0.0 and (float_value(row, "return_1h_pct") or 0.0) >= 8.0:
        flags.append("PRICE_UP_OI_DOWN")
    if (float_value(row, "taker_buy_sell_ratio") or 0.0) >= 2.5:
        flags.append("TAKER_CROWDING")
    if (float_value(row, "return_1h_pct") or 0.0) >= 25.0:
        flags.append("EXTENDED_1H")
    if (float_value(row, "return_24h_pct") or 0.0) >= 100.0:
        flags.append("EXTENDED_24H")
    return tuple(flags)


def compute_actionability_score(
    row: Mapping[str, Any] | Any,
    *,
    continuation_grade: str | None = None,
    ignition_grade: str | None = None,
    risk_flags: Sequence[str] | None = None,
    chase_risk_score: float | None = None,
) -> float:
    if continuation_grade is None:
        continuation_grade = _continuation_grade_fn(row)
    if ignition_grade is None:
        ignition_grade = _ignition_grade_fn(row)
    if chase_risk_score is None:
        chase_risk_score = compute_chase_risk_score(row)
    normalized_risk_flags = normalize_items(risk_flags if risk_flags is not None else get_value(row, "risk_flags"))

    score = 0.0
    if continuation_grade == CONTINUATION_A:
        score += 35.0
    elif continuation_grade == CONTINUATION_B:
        score += 25.0

    if ignition_grade == IGNITION_EXTREME:
        score += 20.0
    elif ignition_grade == IGNITION_A:
        score += 25.0
    elif ignition_grade == IGNITION_B:
        score += 15.0

    if ULTRA_HIGH_CONVICTION_FLAG in normalized_risk_flags:
        score += 15.0

    relative_strength_score = float_value(row, "relative_strength_score") or 0.0
    volume_impulse_score = float_value(row, "volume_impulse_score")
    quality_score = float_value(row, "quality_score") or 0.0
    score += min(15.0, relative_strength_score * 0.15)
    score += min(15.0, (volume_impulse_score or 0.0) * 0.15)
    score += min(10.0, quality_score * 0.10)
    if _bool_value(row, "cross_exchange_confirmed"):
        score += 8.0

    if chase_risk_score >= 80.0:
        score -= 25.0
    elif chase_risk_score >= 60.0:
        score -= 15.0
    elif chase_risk_score >= 40.0:
        score -= 8.0

    if "PRICE_UP_OI_DOWN" in normalized_risk_flags:
        score -= 10.0
    if "FUNDING_OVERHEAT" in normalized_risk_flags and (float_value(row, "return_1h_pct") or 0.0) >= 20.0:
        score -= 10.0

    return round(max(0.0, min(100.0, score)), 4)


def evaluate_signal_v2(row: Mapping[str, Any] | Any) -> SignalV2Result:
    continuation = _continuation_grade_fn(row)
    ignition = _ignition_grade_fn(row)
    ultra_high_conviction = is_ultra_high_conviction_candidate(row)
    chase_risk = compute_chase_risk_score(row)
    risk_flags = compute_risk_flags(
        row,
        ignition_grade=ignition,
        chase_risk_score=chase_risk,
        ultra_high_conviction=ultra_high_conviction,
    )
    actionability = compute_actionability_score(
        row,
        continuation_grade=continuation,
        ignition_grade=ignition,
        risk_flags=risk_flags,
        chase_risk_score=chase_risk,
    )
    return SignalV2Result(
        continuation_grade=continuation,
        ignition_grade=ignition,
        ultra_high_conviction=ultra_high_conviction,
        signal_priority=max(signal_priority_for(continuation, ignition), 3 if ultra_high_conviction else 0),
        risk_flags=risk_flags,
        chase_risk_score=chase_risk,
        actionability_score=actionability,
    )
