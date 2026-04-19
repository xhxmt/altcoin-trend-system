from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _get(row: Mapping[str, Any] | Any, key: str, default: Any = "") -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _format_optional_float(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def build_explain_text(row: Mapping[str, Any] | Any) -> str:
    exchange = _get(row, "exchange", "unknown")
    symbol = row["symbol"] if isinstance(row, Mapping) else getattr(row, "symbol")
    final_score = _get(row, "final_score", 0.0)
    tier = _get(row, "tier", "rejected")
    raw_veto = _get(row, "veto_reason_codes", ())
    if raw_veto is None:
        veto = ()
    elif isinstance(raw_veto, str):
        veto = (raw_veto,)
    else:
        veto = tuple(raw_veto)

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
        "Relative strength:",
        f"RS vs BTC 7d: {_format_optional_float(_get(row, 'rs_btc_7d', None))}",
        f"RS vs ETH 7d: {_format_optional_float(_get(row, 'rs_eth_7d', None))}",
        f"RS vs BTC 30d: {_format_optional_float(_get(row, 'rs_btc_30d', None))}",
        f"RS vs ETH 30d: {_format_optional_float(_get(row, 'rs_eth_30d', None))}",
        "Derivatives:",
        f"OI delta 1h: {_format_optional_float(_get(row, 'oi_delta_1h', None))}",
        f"OI delta 4h: {_format_optional_float(_get(row, 'oi_delta_4h', None))}",
        f"Funding z-score: {_format_optional_float(_get(row, 'funding_zscore', None))}",
        f"Taker buy/sell ratio: {_format_optional_float(_get(row, 'taker_buy_sell_ratio', None))}",
    ]
    if veto:
        lines.append(f"Veto: {', '.join(veto)}")
    else:
        lines.append("Veto: none")
    return "\n".join(lines)
