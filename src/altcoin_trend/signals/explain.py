from __future__ import annotations
from collections.abc import Mapping
from collections.abc import Sequence

from typing import Any

from altcoin_trend.signals.trade_candidate import (
    REACCELERATION_A_RULE,
    REACCELERATION_B_EARLY_VOLUME_RULE,
    REACCELERATION_B_RULE,
    ReaccelerationCandidateRule,
)


def _get(row: Mapping[str, Any] | Any, key: str, default: Any = "") -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _float_value(row: Mapping[str, Any] | Any, key: str) -> float | None:
    value = _get(row, key, None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_optional_float(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _format_optional_int(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "n/a"


def _format_grade(value: Any) -> str:
    if value is None:
        return "-"
    text = str(value).strip()
    return text if text else "-"


def _format_bool(value: Any) -> str:
    return "yes" if bool(value) else "no"


def _normalize_items(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        normalized = value.strip()
        return (normalized,) if normalized else ()
    if isinstance(value, Mapping):
        if not value:
            return ()
        return tuple(f"{key}={item}" for key, item in value.items())
    if isinstance(value, Sequence):
        return tuple(str(item).strip() for item in value if item is not None and str(item).strip())
    normalized = str(value).strip()
    return (normalized,) if normalized else ()


def _format_gate_failure(label: str, value: float | None, *, lower: float | None = None, upper: float | None = None) -> str | None:
    if value is None:
        return f"{label} missing"
    if lower is not None and value < lower:
        return f"{label} {value:.2f} < {lower:.2f}"
    if upper is not None and value > upper:
        return f"{label} {value:.2f} > {upper:.2f}"
    return None


def _append_gate_failure(
    failures: list[str],
    row: Mapping[str, Any] | Any,
    key: str,
    *,
    lower: float | None = None,
    upper: float | None = None,
) -> None:
    failure = _format_gate_failure(key, _float_value(row, key), lower=lower, upper=upper)
    if failure is not None:
        failures.append(failure)


def _reacceleration_rule_failures(
    row: Mapping[str, Any] | Any,
    rule: ReaccelerationCandidateRule,
) -> list[str]:
    failures: list[str] = []
    veto_reason_codes = _normalize_items(_get(row, "veto_reason_codes", None))
    if veto_reason_codes:
        failures.append(f"veto present: {', '.join(veto_reason_codes)}")

    if rule.require_20d_breakout and not bool(_get(row, "breakout_20d", False)):
        failures.append("breakout_20d is false")

    _append_gate_failure(failures, row, "return_1h_pct", lower=rule.min_return_1h_pct, upper=rule.max_return_1h_pct)
    _append_gate_failure(failures, row, "return_4h_pct", lower=rule.min_return_4h_pct, upper=rule.max_return_4h_pct)
    _append_gate_failure(failures, row, "return_24h_pct", lower=rule.min_return_24h_pct, upper=rule.max_return_24h_pct)
    _append_gate_failure(failures, row, "volume_ratio_24h", lower=rule.min_volume_ratio_24h, upper=rule.max_volume_ratio_24h)
    _append_gate_failure(failures, row, "return_24h_percentile", lower=rule.min_return_24h_percentile)
    _append_gate_failure(failures, row, "return_7d_percentile", lower=rule.min_return_7d_percentile)
    if rule.min_return_30d_percentile is not None or rule.max_return_30d_percentile is not None:
        _append_gate_failure(
            failures,
            row,
            "return_30d_percentile",
            lower=rule.min_return_30d_percentile,
            upper=rule.max_return_30d_percentile,
        )
    if rule.min_volume_breakout_score is not None:
        _append_gate_failure(failures, row, "volume_breakout_score", lower=rule.min_volume_breakout_score)
    if rule.max_chase_risk_score is not None:
        _append_gate_failure(failures, row, "chase_risk_score", upper=rule.max_chase_risk_score)
    _append_gate_failure(failures, row, "quality_score", lower=rule.min_quality_score)
    return failures


def _format_gate_status(name: str, failures: list[str]) -> str:
    if not failures:
        return f"{name} gates: pass"
    shown = "; ".join(failures[:4])
    if len(failures) > 4:
        shown += f"; +{len(failures) - 4} more"
    return f"{name} gates: fail ({shown})"


def _driver_names(row: Mapping[str, Any] | Any) -> str:
    drivers: list[str] = []
    if (_float_value(row, "oi_delta_1h") or 0.0) > 0.0 or (_float_value(row, "oi_delta_4h") or 0.0) > 0.0:
        drivers.append("OI")
    if (_float_value(row, "relative_strength_score") or 0.0) >= 85.0 or (_float_value(row, "return_7d_percentile") or 0.0) >= 0.90:
        drivers.append("relative strength")
    if (_float_value(row, "trend_score") or 0.0) >= 80.0 or bool(_get(row, "breakout_20d", False)):
        drivers.append("trend")
    if (
        (_float_value(row, "volume_breakout_score") or 0.0) >= 70.0
        or (_float_value(row, "volume_ratio_24h") or 0.0) >= 2.5
        or (_float_value(row, "volume_impulse_score") or 0.0) >= 60.0
    ):
        drivers.append("volume")
    return ", ".join(drivers) if drivers else "none"


def _format_driver_values(row: Mapping[str, Any] | Any) -> str:
    return (
        "Driver values: "
        f"trend={_format_optional_float(_get(row, 'trend_score', None))}, "
        f"volume_breakout={_format_optional_float(_get(row, 'volume_breakout_score', None))}, "
        f"relative_strength={_format_optional_float(_get(row, 'relative_strength_score', None))}, "
        f"OI 1h={_format_optional_float(_get(row, 'oi_delta_1h', None))}, "
        f"OI 4h={_format_optional_float(_get(row, 'oi_delta_4h', None))}"
    )


def _build_reacceleration_detail_lines(row: Mapping[str, Any] | Any) -> list[str]:
    grade = _format_grade(_get(row, "reacceleration_grade", None))
    if grade not in {"A", "B"}:
        return []

    a_failures = _reacceleration_rule_failures(row, REACCELERATION_A_RULE)
    classic_b_failures = _reacceleration_rule_failures(row, REACCELERATION_B_RULE)
    early_volume_b_failures = _reacceleration_rule_failures(row, REACCELERATION_B_EARLY_VOLUME_RULE)

    if grade == "A":
        branch = "A" if not a_failures else "A (stored; gates unavailable)"
    elif not classic_b_failures:
        branch = "classic B"
    elif not early_volume_b_failures:
        branch = "early-volume B"
    else:
        branch = "B (stored; gates unavailable)"

    if grade == "A":
        why_not_a = "Why not A: already A"
    elif a_failures:
        why_not_a = f"Why not A: {'; '.join(a_failures[:4])}"
    else:
        why_not_a = "Why not A: A gates passed but stored grade is B"

    chase_risk = _float_value(row, "chase_risk_score")
    if chase_risk is None:
        chase_suppression = "Chase suppression: unknown"
    elif chase_risk > 40.0:
        chase_suppression = f"Chase suppression: yes ({chase_risk:.2f} > 40.00)"
    else:
        chase_suppression = f"Chase suppression: no ({chase_risk:.2f} <= 40.00)"

    return [
        "Reacceleration details:",
        f"Branch: {branch}",
        _format_gate_status("Classic B", classic_b_failures),
        _format_gate_status("Early-volume B", early_volume_b_failures),
        why_not_a,
        f"Drivers: {_driver_names(row)}",
        _format_driver_values(row),
        chase_suppression,
    ]


def build_explain_text(row: Mapping[str, Any] | Any) -> str:
    exchange = _get(row, "exchange", "unknown")
    symbol = row["symbol"] if isinstance(row, Mapping) else getattr(row, "symbol")
    final_score = _get(row, "final_score", 0.0)
    tier = _get(row, "tier", "rejected")
    veto = _normalize_items(_get(row, "veto_reason_codes", ()))
    risk_flags = _normalize_items(_get(row, "risk_flags", ()))

    lines = [
        f"{exchange}:{symbol}",
        f"Score: {final_score}",
        f"Tier: {tier}",
        "Signal v2:",
        f"Priority: {_format_optional_int(_get(row, 'signal_priority', None))}",
        f"Continuation: {_format_grade(_get(row, 'continuation_grade', None))}",
        f"Ignition: {_format_grade(_get(row, 'ignition_grade', None))}",
        f"Reacceleration: {_format_grade(_get(row, 'reacceleration_grade', None))}",
        f"Ultra high conviction: {_format_bool(_get(row, 'ultra_high_conviction', False))}",
        f"Actionability: {_format_optional_float(_get(row, 'actionability_score', None))}",
        f"Chase risk: {_format_optional_float(_get(row, 'chase_risk_score', None))}",
        f"Risk flags: {', '.join(risk_flags) if risk_flags else 'none'}",
        *_build_reacceleration_detail_lines(row),
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
