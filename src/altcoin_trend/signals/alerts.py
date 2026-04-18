from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _get(row: Mapping[str, Any] | Any, key: str, default: Any = "") -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _normalize_items(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    return (str(value),)


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
        current_time = now or datetime.now(timezone.utc)
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
        current_time = now or datetime.now(timezone.utc)
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
