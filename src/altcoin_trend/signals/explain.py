from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _get(row: Mapping[str, Any] | Any, key: str, default: Any = "") -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def build_explain_text(row: Mapping[str, Any] | Any) -> str:
    symbol = _get(row, "symbol", "UNKNOWN")
    final_score = _get(row, "final_score", 0.0)
    tier = _get(row, "tier", "rejected")
    primary_reason = _get(row, "primary_reason", "")
    veto = tuple(_get(row, "veto", ()))

    lines = [
        f"Symbol: {symbol}",
        f"Score: {final_score}",
        f"Tier: {tier}",
        f"Trend: {_get(row, 'trend', 'n/a')}",
        f"Volume: {_get(row, 'volume', 'n/a')}",
        f"Relative: {_get(row, 'relative', 'n/a')}",
        f"Derivatives: {_get(row, 'derivatives', 'n/a')}",
        f"Quality: {_get(row, 'quality', 'n/a')}",
    ]
    if veto:
        lines.append(f"Veto: {', '.join(veto)}")
    else:
        lines.append("Veto: none")
    if primary_reason:
        lines.append(f"Primary reason: {primary_reason}")
    return "\n".join(lines)
