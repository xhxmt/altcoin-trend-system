from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from altcoin_trend.signals.state import evaluate_transition


MAX_SIGNAL_V2_COOLDOWN_SECONDS = 4 * 60 * 60


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


def is_high_value_signal(row: Mapping[str, Any] | Any) -> bool:
    tier = str(_get(row, "tier", "")).strip()
    if tier not in {"watchlist", "strong"}:
        return False

    try:
        trend_score = float(_get(row, "trend_score", 0.0))
        relative_strength_score = float(_get(row, "relative_strength_score", 0.0))
        derivatives_score = float(_get(row, "derivatives_score", 0.0))
        quality_score = float(_get(row, "quality_score", 0.0))
        volume_breakout_score = float(_get(row, "volume_breakout_score", 0.0))
    except (TypeError, ValueError):
        return False

    veto_reason_codes = _normalize_items(_get(row, "veto_reason_codes", None))
    return (
        trend_score >= 75.0
        and relative_strength_score >= 70.0
        and derivatives_score >= 55.0
        and quality_score >= 80.0
        and volume_breakout_score >= 40.0
        and not veto_reason_codes
    )


def is_explosive_move_early_signal(row: Mapping[str, Any] | Any) -> bool:
    try:
        return_1h_pct = float(_get(row, "return_1h_pct", 0.0))
        return_4h_pct = float(_get(row, "return_4h_pct", 0.0))
        return_24h_percentile = float(_get(row, "return_24h_percentile", 0.0))
        relative_strength_score = float(_get(row, "relative_strength_score", 0.0))
        quality_score = float(_get(row, "quality_score", 0.0))
    except (TypeError, ValueError):
        return False

    veto_reason_codes = _normalize_items(_get(row, "veto_reason_codes", None))
    return (
        (return_1h_pct >= 12.0 or return_4h_pct >= 20.0)
        and return_24h_percentile >= 0.97
        and relative_strength_score >= 90.0
        and quality_score >= 80.0
        and not veto_reason_codes
    )


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

    def _display_score(key: str) -> Any:
        value = _get(row, key, None)
        return "n/a" if value is None else value

    lines = [
        f"[STRONG] {symbol} {display_exchange}",
        f"Final score: {final_score}",
        "Score breakdown:",
        f"Trend: {_display_score('trend_score')}",
        f"Volume breakout: {_display_score('volume_breakout_score')}",
        f"Relative strength: {_display_score('relative_strength_score')}",
        f"Derivatives: {_display_score('derivatives_score')}",
        f"Quality: {_display_score('quality_score')}",
    ]
    for label, key in (
        ("OI delta 1h", "oi_delta_1h"),
        ("OI delta 4h", "oi_delta_4h"),
        ("Funding z-score", "funding_zscore"),
        ("Taker buy/sell ratio", "taker_buy_sell_ratio"),
    ):
        value = _get(row, key, None)
        if value is not None:
            lines.append(f"{label}: {value}")
    lines.extend(
        [
            f"Reasons: {', '.join(reasons) if reasons else 'none'}",
            f"Risks: {', '.join(risks) if risks else 'none'}",
        ]
    )
    return "\n".join(lines)


def build_explosive_move_early_alert_message(row: Mapping[str, Any] | Any) -> str:
    exchange = str(_get(row, "exchange", "unknown"))
    symbol = str(_get(row, "symbol", "unknown"))
    display_exchange = exchange.title()

    def _display_value(key: str) -> Any:
        value = _get(row, key, None)
        return "n/a" if value is None else value

    lines = [
        f"[EXPLOSIVE_MOVE_EARLY] {symbol} {display_exchange}",
        f"Final score: {_display_value('final_score')}",
        f"Tier: {_display_value('tier')}",
        "Move context:",
        f"Return 1h: {_display_value('return_1h_pct')}",
        f"Return 4h: {_display_value('return_4h_pct')}",
        f"Return 24h percentile: {_display_value('return_24h_percentile')}",
        f"Relative strength: {_display_value('relative_strength_score')}",
        f"Volume breakout: {_display_value('volume_breakout_score')}",
        f"Quality: {_display_value('quality_score')}",
    ]
    return "\n".join(lines)


def _alert_priority_for_type(alert_type: str, row: Mapping[str, Any] | Any | None = None) -> str:
    if alert_type == "ignition_extreme":
        return "P1"
    if alert_type == "continuation_confirmed":
        continuation_grade = _get(row, "continuation_grade", None) if row is not None else None
        return "P1" if continuation_grade == "A" else "P2"
    if alert_type == "ignition_detected":
        ignition_grade = _get(row, "ignition_grade", None) if row is not None else None
        return "P2" if ignition_grade == "A" else "P3"
    if alert_type == "exhaustion_risk":
        return "P2"
    return "P3"


def _cooldown_for_priority(priority: str, default_seconds: int) -> int:
    if priority == "P1":
        return 3600
    if priority == "P2":
        return 7200
    return 14400


def _v2_alert_type(row: Mapping[str, Any] | Any) -> str | None:
    continuation_grade = _get(row, "continuation_grade", None)
    ignition_grade = _get(row, "ignition_grade", None)
    risk_flags = set(_normalize_items(_get(row, "risk_flags", None)))
    try:
        chase_risk_score = float(_get(row, "chase_risk_score", 0.0))
    except (TypeError, ValueError):
        chase_risk_score = 0.0

    if ignition_grade == "EXTREME":
        return "ignition_extreme"
    if continuation_grade in {"A", "B"}:
        return "continuation_confirmed"
    if ignition_grade in {"A", "B"}:
        return "ignition_detected"
    if chase_risk_score >= 80.0 or {"FUNDING_OVERHEAT", "TAKER_CROWDING"} & risk_flags:
        return "exhaustion_risk"
    return None


def build_signal_v2_alert_message(row: Mapping[str, Any] | Any, alert_type: str) -> str:
    symbol = str(_get(row, "symbol", "unknown"))
    continuation_grade = _get(row, "continuation_grade", None)
    ignition_grade = _get(row, "ignition_grade", None)
    if alert_type == "ignition_extreme":
        header = f"[IGNITION_EXTREME] {symbol}"
    elif alert_type == "ignition_detected":
        header = f"[IGNITION_{ignition_grade}] {symbol}"
    elif alert_type == "continuation_confirmed":
        header = f"[CONTINUATION_{continuation_grade}] {symbol}"
    else:
        header = f"[EXHAUSTION_RISK] {symbol}"

    def display(key: str) -> Any:
        value = _get(row, key, None)
        return "n/a" if value is None else value

    risks = _normalize_items(_get(row, "risk_flags", None))
    return "\n".join(
        [
            header,
            f"1h {display('return_1h_pct')} | 24h {display('return_24h_pct')}",
            f"RS {display('relative_strength_score')} | Vol impulse {display('volume_impulse_score')} | Deriv {display('derivatives_score')}",
            f"Cross-exchange: {'yes' if bool(_get(row, 'cross_exchange_confirmed', False)) else 'no'}",
            f"Chase risk: {display('chase_risk_score')}",
            f"Actionability: {display('actionability_score')}",
            f"Risks: {', '.join(risks) if risks else 'none'}",
        ]
    )


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
    events_by_key: dict[tuple[int, str], Mapping[str, Any]] = {}
    for event in recent_events:
        if "asset_id" not in event or "alert_type" not in event or "ts" not in event:
            continue
        key = _recent_event_key(event)
        existing = events_by_key.get(key)
        if existing is None or _require_aware_datetime(event["ts"]) > _require_aware_datetime(existing["ts"]):
            events_by_key[key] = event
    alert_rows: list[dict[str, Any]] = []

    for row in rank_rows:
        asset_id = int(_get(row, "asset_id"))
        current_tier = str(_get(row, "tier", "rejected"))
        previous_tier = _previous_tier_for_asset(asset_id, recent_events)
        v2_alert_type = _v2_alert_type(row)
        if v2_alert_type is not None:
            priority = _alert_priority_for_type(v2_alert_type, row)
            effective_cooldown = _cooldown_for_priority(priority, cooldown_seconds)
            recent_event = events_by_key.get((asset_id, v2_alert_type))
            if recent_event is None or not _event_recent_enough(recent_event, current_time, effective_cooldown):
                alert_rows.append(
                    {
                        "ts": current_time,
                        "asset_id": asset_id,
                        "symbol": str(_get(row, "symbol")),
                        "alert_type": v2_alert_type,
                        "final_score": float(_get(row, "final_score", 0.0)),
                        "message": build_signal_v2_alert_message(row, v2_alert_type),
                        "payload": {
                            "exchange": _get(row, "exchange", "unknown"),
                            "current_tier": current_tier,
                            "previous_tier": previous_tier,
                            "rank": _get(row, "rank", None),
                            "priority": priority,
                            "grades": {
                                "continuation": _get(row, "continuation_grade", None),
                                "ignition": _get(row, "ignition_grade", None),
                            },
                            "continuation_grade": _get(row, "continuation_grade", None),
                            "ignition_grade": _get(row, "ignition_grade", None),
                            "signal_priority": _get(row, "signal_priority", None),
                            "actionability_score": _get(row, "actionability_score", None),
                            "chase_risk_score": _get(row, "chase_risk_score", None),
                            "risk_flags": list(_normalize_items(_get(row, "risk_flags", None))),
                            "cross_exchange_confirmed": bool(_get(row, "cross_exchange_confirmed", False)),
                        },
                        "delivery_status": "pending",
                        "delivery_error": None,
                    }
                )
        if is_explosive_move_early_signal(row):
            recent_event = events_by_key.get((asset_id, "explosive_move_early"))
            if recent_event is None or not _event_recent_enough(recent_event, current_time, cooldown_seconds):
                alert_rows.append(
                    {
                        "ts": current_time,
                        "asset_id": asset_id,
                        "symbol": str(_get(row, "symbol")),
                        "alert_type": "explosive_move_early",
                        "final_score": float(_get(row, "final_score", 0.0)),
                        "message": build_explosive_move_early_alert_message(row),
                        "payload": {
                            "exchange": _get(row, "exchange", "unknown"),
                            "current_tier": current_tier,
                            "previous_tier": previous_tier,
                            "rank": _get(row, "rank", None),
                        },
                        "delivery_status": "pending",
                        "delivery_error": None,
                    }
                )
        if v2_alert_type is not None:
            continue
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
        if decision.alert_type in {"strong_trend", "watchlist_enter", "breakout_confirmed"} and not is_high_value_signal(row):
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
