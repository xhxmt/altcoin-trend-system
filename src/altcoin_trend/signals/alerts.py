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


def _float_or_default(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _display_exchange(row: Mapping[str, Any] | Any) -> str:
    value = _get(row, "exchange", "unknown")
    return str(value) if value else "unknown"


def _display_long_signal_strength(row: Mapping[str, Any] | Any) -> str:
    value = _get(row, "actionability_score", None)
    if value is None:
        value = _get(row, "final_score", None)
    return "n/a" if value is None else str(value)


def _display_alert_type(alert_type: str) -> str:
    labels = {
        "strong_trend": "强趋势",
        "watchlist_enter": "进入观察",
        "breakout_confirmed": "突破确认",
        "risk_downgrade": "风险降级",
        "explosive_move_early": "早期爆发",
        "continuation_confirmed": "趋势延续确认",
        "ignition_detected": "点火信号",
        "ignition_extreme": "极端点火",
        "reacceleration_detected": "再加速突破",
        "ultra_high_conviction": "超高置信",
        "exhaustion_risk": "过热风险",
    }
    return labels.get(alert_type, alert_type or "未知信号")


def _build_long_signal_message(row: Mapping[str, Any] | Any, alert_type: str) -> str:
    symbol = str(_get(row, "symbol", "unknown"))
    strength = _display_long_signal_strength(row)
    signal = _display_alert_type(alert_type)
    return f"币种：{symbol}\n信号：{signal}\n做多信号强度：{strength}/100"


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


def build_strong_alert_message(row: Mapping[str, Any] | Any, alert_type: str = "strong_trend") -> str:
    return _build_long_signal_message(row, alert_type)


def build_explosive_move_early_alert_message(row: Mapping[str, Any] | Any) -> str:
    return _build_long_signal_message(row, "explosive_move_early")


def _alert_priority_for_type(alert_type: str, row: Mapping[str, Any] | Any | None = None) -> str:
    if alert_type == "ultra_high_conviction":
        return "P1"
    if alert_type == "ignition_extreme":
        return "P1"
    if alert_type == "continuation_confirmed":
        continuation_grade = _get(row, "continuation_grade", None) if row is not None else None
        return "P1" if continuation_grade == "A" else "P2"
    if alert_type == "ignition_detected":
        ignition_grade = _get(row, "ignition_grade", None) if row is not None else None
        return "P2" if ignition_grade == "A" else "P3"
    if alert_type == "reacceleration_detected":
        reacceleration_grade = _get(row, "reacceleration_grade", None) if row is not None else None
        return "P2" if reacceleration_grade == "A" else "P3"
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
    reacceleration_grade = _get(row, "reacceleration_grade", None)
    risk_flags = set(_normalize_items(_get(row, "risk_flags", None)))
    try:
        chase_risk_score = float(_get(row, "chase_risk_score", 0.0))
    except (TypeError, ValueError):
        chase_risk_score = 0.0

    if bool(_get(row, "ultra_high_conviction", False)) or "ULTRA_HIGH_CONVICTION" in risk_flags:
        return "ultra_high_conviction"
    if ignition_grade == "EXTREME":
        return "ignition_extreme"
    if continuation_grade in {"A", "B"}:
        return "continuation_confirmed"
    if ignition_grade in {"A", "B"}:
        return "ignition_detected"
    if reacceleration_grade in {"A", "B"}:
        return "reacceleration_detected"
    if chase_risk_score >= 80.0 or {"FUNDING_OVERHEAT", "TAKER_CROWDING"} & risk_flags:
        return "exhaustion_risk"
    return None


def _signal_family(alert_type: str) -> str:
    if alert_type == "ultra_high_conviction":
        return "ultra_high_conviction"
    if alert_type in {"ignition_extreme", "ignition_detected"}:
        return "ignition"
    if alert_type == "reacceleration_detected":
        return "reacceleration"
    if alert_type == "continuation_confirmed":
        return "continuation"
    return alert_type


def _alert_type_severity(alert_type: str) -> int:
    if alert_type == "ultra_high_conviction":
        return 4
    if alert_type == "ignition_extreme":
        return 3
    if alert_type in {"continuation_confirmed", "ignition_detected", "reacceleration_detected"}:
        return 2
    if alert_type == "exhaustion_risk":
        return 1
    return 0


def _exchange_signal_label(row: Mapping[str, Any] | Any, family: str) -> str:
    if family == "ultra_high_conviction":
        return "ULTRA_HIGH_CONVICTION"
    if family == "ignition":
        ignition = _get(row, "ignition_grade", None)
        return f"IGNITION_{ignition}" if ignition else "NONE"
    if family == "reacceleration":
        reacceleration = _get(row, "reacceleration_grade", None)
        return f"REACCELERATION_{reacceleration}" if reacceleration else "NONE"
    if family == "continuation":
        continuation = _get(row, "continuation_grade", None)
        return f"CONTINUATION_{continuation}" if continuation else "NONE"
    if family == "exhaustion_risk":
        return "EXHAUSTION_RISK"
    return "NONE"


def _v2_best_key(row: Mapping[str, Any] | Any, alert_type: str) -> tuple[int, float, float, float]:
    return (
        _alert_type_severity(alert_type),
        _float_or_default(_get(row, "actionability_score", 0.0)),
        _float_or_default(_get(row, "signal_priority", 0.0)),
        _float_or_default(_get(row, "final_score", 0.0)),
    )


def build_signal_v2_alert_message(
    row: Mapping[str, Any] | Any,
    alert_type: str,
    per_exchange_signals: Mapping[str, str] | None = None,
) -> str:
    return _build_long_signal_message(row, alert_type)


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
    v2_events_by_symbol_family: dict[tuple[str, str], Mapping[str, Any]] = {}
    for event in recent_events:
        if "asset_id" not in event or "alert_type" not in event or "ts" not in event:
            continue
        event_ts = _require_aware_datetime(event["ts"])
        asset_key = _recent_event_key(event)
        existing = events_by_key.get(asset_key)
        if existing is None or event_ts > _require_aware_datetime(existing["ts"]):
            events_by_key[asset_key] = event

        alert_type = str(event["alert_type"])
        if _alert_type_severity(alert_type) <= 0:
            continue
        symbol = event.get("symbol")
        if not symbol:
            payload = event.get("payload") or {}
            symbol = payload.get("symbol") if isinstance(payload, Mapping) else None
        if not symbol:
            continue
        symbol_family_key = (str(symbol), _signal_family(alert_type))
        existing_v2 = v2_events_by_symbol_family.get(symbol_family_key)
        if existing_v2 is None or event_ts > _require_aware_datetime(existing_v2["ts"]):
            v2_events_by_symbol_family[symbol_family_key] = event
    alert_rows: list[dict[str, Any]] = []
    best_v2_by_symbol_family: dict[tuple[str, str], Mapping[str, Any]] = {}
    per_exchange_by_symbol_family: dict[tuple[str, str], dict[str, str]] = {}
    asset_ids_by_symbol_family: dict[tuple[str, str], list[int]] = {}
    for candidate_row in rank_rows:
        candidate_type = _v2_alert_type(candidate_row)
        if candidate_type is None:
            continue
        symbol = str(_get(candidate_row, "symbol"))
        family = _signal_family(candidate_type)
        key = (symbol, family)
        exchange = _display_exchange(candidate_row)
        per_exchange_by_symbol_family.setdefault(key, {})[exchange] = _exchange_signal_label(candidate_row, family)
        try:
            asset_ids_by_symbol_family.setdefault(key, []).append(int(_get(candidate_row, "asset_id")))
        except (TypeError, ValueError):
            pass
        current_best = best_v2_by_symbol_family.get(key)
        current_best_type = _v2_alert_type(current_best) if current_best is not None else None
        if current_best is None or (
            current_best_type is not None and _v2_best_key(candidate_row, candidate_type) > _v2_best_key(current_best, current_best_type)
        ):
            best_v2_by_symbol_family[key] = candidate_row

    for row in rank_rows:
        asset_id = int(_get(row, "asset_id"))
        current_tier = str(_get(row, "tier", "rejected"))
        previous_tier = _previous_tier_for_asset(asset_id, recent_events)
        v2_alert_type = _v2_alert_type(row)
        if v2_alert_type is not None:
            symbol = str(_get(row, "symbol"))
            family = _signal_family(v2_alert_type)
            key = (symbol, family)
            if best_v2_by_symbol_family.get(key) is row:
                priority = _alert_priority_for_type(v2_alert_type, row)
                effective_cooldown = _cooldown_for_priority(priority, cooldown_seconds)
                recent_event = v2_events_by_symbol_family.get(key) or events_by_key.get((asset_id, v2_alert_type))
                if recent_event is None or not _event_recent_enough(recent_event, current_time, effective_cooldown):
                    per_exchange_signals = per_exchange_by_symbol_family.get(key, {})
                    alert_rows.append(
                        {
                            "ts": current_time,
                            "asset_id": asset_id,
                            "symbol": symbol,
                            "alert_type": v2_alert_type,
                            "final_score": float(_get(row, "final_score", 0.0)),
                            "message": build_signal_v2_alert_message(row, v2_alert_type, per_exchange_signals),
                            "payload": {
                                "exchange": _display_exchange(row),
                                "current_tier": current_tier,
                                "previous_tier": previous_tier,
                                "rank": _get(row, "rank", None),
                                "priority": priority,
                                "grades": {
                                    "continuation": _get(row, "continuation_grade", None),
                                    "ignition": _get(row, "ignition_grade", None),
                                    "reacceleration": _get(row, "reacceleration_grade", None),
                                },
                                "continuation_grade": _get(row, "continuation_grade", None),
                                "ignition_grade": _get(row, "ignition_grade", None),
                                "reacceleration_grade": _get(row, "reacceleration_grade", None),
                                "ultra_high_conviction": bool(_get(row, "ultra_high_conviction", False)),
                                "signal_priority": _get(row, "signal_priority", None),
                                "actionability_score": _get(row, "actionability_score", None),
                                "chase_risk_score": _get(row, "chase_risk_score", None),
                                "risk_flags": list(_normalize_items(_get(row, "risk_flags", None))),
                                "cross_exchange_confirmed": bool(_get(row, "cross_exchange_confirmed", False)),
                                "per_exchange_signals": per_exchange_signals,
                                "asset_ids": asset_ids_by_symbol_family.get(key, []),
                                "exchanges": list(per_exchange_signals),
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
                            "exchange": _display_exchange(row),
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

        message = build_strong_alert_message(row, decision.alert_type)
        payload = {
            "exchange": _display_exchange(row),
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
