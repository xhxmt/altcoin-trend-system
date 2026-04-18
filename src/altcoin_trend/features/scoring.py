from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from altcoin_trend.features.derivatives import clamp_score as clamp_derivatives_score
from altcoin_trend.features.quality import clamp_score as clamp_quality_score
from altcoin_trend.features.relative_strength import clamp_score as clamp_relative_strength_score
from altcoin_trend.features.trend import clamp_score as clamp_trend_score
from altcoin_trend.features.volume import clamp_score as clamp_volume_score


@dataclass(frozen=True)
class ScoreInput:
    trend: float
    volume: float
    relative: float
    derivatives: float
    quality: float
    veto: Sequence[str]


@dataclass(frozen=True)
class ScoreResult:
    final_score: float
    tier: str
    primary_reason: str
    trend: float
    volume: float
    relative: float
    derivatives: float
    quality: float
    veto: tuple[str, ...]


def tier_for_score(final_score: float) -> str:
    if final_score >= 85:
        return "strong"
    if final_score >= 75:
        return "watchlist"
    if final_score >= 60:
        return "monitor"
    return "rejected"


def compute_final_score(score_input: ScoreInput) -> ScoreResult:
    trend = clamp_trend_score(score_input.trend)
    volume = clamp_volume_score(score_input.volume)
    relative = clamp_relative_strength_score(score_input.relative)
    derivatives = clamp_derivatives_score(score_input.derivatives)
    quality = clamp_quality_score(score_input.quality)

    final_score = round(
        0.35 * trend
        + 0.25 * volume
        + 0.20 * relative
        + 0.15 * derivatives
        + 0.05 * quality,
        4,
    )
    veto = tuple(score_input.veto)
    if veto:
        return ScoreResult(
            final_score=final_score,
            tier="rejected",
            primary_reason=veto[0],
            trend=trend,
            volume=volume,
            relative=relative,
            derivatives=derivatives,
            quality=quality,
            veto=veto,
        )
    return ScoreResult(
        final_score=final_score,
        tier=tier_for_score(final_score),
        primary_reason="",
        trend=trend,
        volume=volume,
        relative=relative,
        derivatives=derivatives,
        quality=quality,
        veto=veto,
    )
