from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _get(row: Mapping[str, Any] | Any, key: str, default: Any = "") -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def build_explain_text(row: Mapping[str, Any] | Any) -> str:
    exchange = _get(row, "exchange", "unknown")
    symbol = row["symbol"] if isinstance(row, Mapping) else getattr(row, "symbol")
    final_score = _get(row, "final_score", 0.0)
    tier = _get(row, "tier", "rejected")
    veto = tuple(_get(row, "veto_reason_codes", ()))

    lines = [
        f"{exchange}:{symbol}",
        f"Score: {final_score}",
        f"Tier: {tier}",
        "Breakdown:",
        f"Trend: {_get(row, 'trend_score', 'n/a')}",
        f"Volume breakout: {_get(row, 'volume_breakout_score', 'n/a')}",
        f"Relative strength: {_get(row, 'relative_strength_score', 'n/a')}",
        f"Derivatives: {_get(row, 'derivatives_score', 'n/a')}",
        f"Quality: {_get(row, 'quality_score', 'n/a')}",
    ]
    if veto:
        lines.append(f"Veto: {', '.join(veto)}")
    else:
        lines.append("Veto: none")
    return "\n".join(lines)
