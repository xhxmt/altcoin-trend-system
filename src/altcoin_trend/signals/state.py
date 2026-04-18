from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

_TIER_ORDER = {"rejected": 0, "monitor": 1, "watchlist": 2, "strong": 3}


@dataclass(frozen=True)
class AlertDecision:
    alert_type: str
    should_alert: bool


def _tier_rank(tier: str) -> int:
    return _TIER_ORDER.get(tier, -1)


def _normalize_codes(codes: Sequence[str] | str | None) -> tuple[str, ...]:
    if codes is None:
        return ()
    if isinstance(codes, str):
        return (codes,)
    return tuple(codes)


def evaluate_transition(
    previous_tier: str,
    current_tier: str,
    breakout_confirmed: bool,
    oi_confirmed: bool,
    veto_reason_codes: Sequence[str] | str | None,
) -> AlertDecision:
    veto_codes = _normalize_codes(veto_reason_codes)
    previous_rank = _tier_rank(previous_tier)
    current_rank = _tier_rank(current_tier)
    has_veto = bool(veto_codes)

    if previous_tier in {"strong", "watchlist"} and (has_veto or current_rank < previous_rank):
        return AlertDecision(alert_type="risk_downgrade", should_alert=True)

    if (
        previous_tier != "strong"
        and current_tier == "strong"
        and breakout_confirmed
        and oi_confirmed
        and not has_veto
    ):
        return AlertDecision(alert_type="strong_trend", should_alert=True)

    if previous_rank < _TIER_ORDER["watchlist"] and current_tier == "watchlist" and not has_veto:
        return AlertDecision(alert_type="watchlist_enter", should_alert=True)

    if breakout_confirmed and not has_veto:
        return AlertDecision(alert_type="breakout_confirmed", should_alert=True)

    return AlertDecision(alert_type="", should_alert=False)
