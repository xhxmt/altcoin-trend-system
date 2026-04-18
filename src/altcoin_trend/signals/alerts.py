from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from altcoin_trend.signals.state import evaluate_transition


def _get(row: Mapping[str, Any] | Any, key: str, default: Any = "") -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _normalize_items(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        normalized = value.strip()
        return (normalized,) if normalized else ()
    if isinstance(value, Sequence):
        normalized = tuple(str(item).strip() for item in value)
        return tuple(item for item in normalized if item)
    normalized = str(value).strip()
    return (normalized,) if normalized else ()


def _require_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value


@dataclass
class AlertCooldown:
    cooldown_seconds: int
    _last_sent: dict[tuple[str, str, str], datetime] = field(default_factory=dict, init=False, repr=False)

    def should_send(
        self,
        exchange: str,
        symbol: str,
        alert_type: str,
        now: datetime | None = None,
    ) -> bool:
        current_time = _require_aware_datetime(now or datetime.now(timezone.utc))
        last_sent = self._last_sent.get((exchange, symbol, alert_type))
        if last_sent is None:
            return True
        return (current_time - last_sent).total_seconds() >= self.cooldown_seconds

    def record_sent(
        self,
        exchange: str,
        symbol: str,
        alert_type: str,
        now: datetime | None = None,
    ) -> None:
        current_time = _require_aware_datetime(now or datetime.now(timezone.utc))
        self._last_sent[(exchange, symbol, alert_type)] = current_time


def build_strong_alert_message(row: Mapping[str, Any] | Any) -> str:
    exchange = str(_get(row, "exchange", "unknown"))
    symbol = str(_get(row, "symbol", "unknown"))
    display_exchange = exchange.title()
    final_score = _get(row, "final_score", 0.0)

    reasons = _normalize_items(_get(row, "reasons", None))
    if not reasons:
        reasons = _normalize_items(_get(row, "primary_reason", None))

    risks = _normalize_items(_get(row, "risks", None))
    if not risks:
        risks = _normalize_items(_get(row, "veto_reason_codes", None))

    lines = [
        f"[STRONG] {symbol} {display_exchange}",
        f"Final score: {final_score}",
        "Score breakdown:",
        f"Trend: {_get(row, 'trend_score', 'n/a')}",
        f"Volume breakout: {_get(row, 'volume_breakout_score', 'n/a')}",
        f"Relative strength: {_get(row, 'relative_strength_score', 'n/a')}",
        f"Derivatives: {_get(row, 'derivatives_score', 'n/a')}",
        f"Quality: {_get(row, 'quality_score', 'n/a')}",
        f"Reasons: {', '.join(reasons) if reasons else 'none'}",
        f"Risks: {', '.join(risks) if risks else 'none'}",
    ]
    return "\n".join(lines)


def _recent_event_key(event: Mapping[str, Any]) -> tuple[int, str]:
    return (int(event["asset_id"]), str(event["alert_type"]))


def _event_recent_enough(event: Mapping[str, Any], now: datetime, cooldown_seconds: int) -> bool:
    event_ts = _require_aware_datetime(event["ts"])
    return (now - event_ts).total_seconds() < cooldown_seconds


def _previous_tier_for_asset(asset_id: int, recent_events: list[Mapping[str, Any]]) -> str:
    asset_events = [event for event in recent_events if int(event.get("asset_id", -1)) == asset_id]
    if not asset_events:
        return "monitor"
    latest = max(asset_events, key=lambda event: event["ts"])
    payload = latest.get("payload") or {}
    if isinstance(payload, Mapping):
        current_tier = payload.get("current_tier")
        if isinstance(current_tier, str) and current_tier:
            return current_tier
    return "monitor"


def build_alert_event_rows(
    rank_rows: list[Mapping[str, Any]],
    recent_events: list[Mapping[str, Any]],
    now: datetime,
    cooldown_seconds: int,
) -> list[dict[str, Any]]:
    current_time = _require_aware_datetime(now)
    events_by_key = {
        _recent_event_key(event): event
        for event in recent_events
        if "asset_id" in event and "alert_type" in event and "ts" in event
    }
    alert_rows: list[dict[str, Any]] = []

    for row in rank_rows:
        asset_id = int(_get(row, "asset_id"))
        current_tier = str(_get(row, "tier", "rejected"))
        previous_tier = _previous_tier_for_asset(asset_id, recent_events)
        positive_breakout = current_tier == "strong" and previous_tier not in {"strong", "watchlist"}
        decision = evaluate_transition(
            previous_tier=previous_tier,
            current_tier=current_tier,
            breakout_confirmed=positive_breakout,
            oi_confirmed=current_tier == "strong",
            veto_reason_codes=_get(row, "veto_reason_codes", ()),
        )
        if not decision.should_alert:
            continue

        recent_event = events_by_key.get((asset_id, decision.alert_type))
        if recent_event is not None and _event_recent_enough(recent_event, current_time, cooldown_seconds):
            continue

        message = build_strong_alert_message(row)
        payload = {
            "exchange": _get(row, "exchange", "unknown"),
            "current_tier": current_tier,
            "previous_tier": previous_tier,
            "rank": _get(row, "rank", None),
        }
        alert_rows.append(
            {
                "ts": current_time,
                "asset_id": asset_id,
                "symbol": str(_get(row, "symbol")),
                "alert_type": decision.alert_type,
                "final_score": float(_get(row, "final_score", 0.0)),
                "message": message,
                "payload": payload,
                "delivery_status": "pending",
                "delivery_error": None,
            }
        )

    return alert_rows
