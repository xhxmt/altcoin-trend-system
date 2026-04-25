from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
from sqlalchemy import Engine, text

from altcoin_trend.config import load_settings
from altcoin_trend.db import build_engine
from altcoin_trend.signals.trade_candidate import ULTRA_HIGH_CONVICTION_RULE
from altcoin_trend.trade_backtest import (
    _coerce_utc_datetime,
    _prepare_feature_frame,
    summarize_signal_v2_groups,
)

sys.modules.setdefault(__name__, ModuleType(__name__))

DEFAULT_OUTPUT_ROOT = "artifacts/autoresearch"
SUMMARY_FILENAME = "summary.json"
SIGNALS_FILENAME = "signals.csv"
METADATA_FILENAME = "metadata.json"
README_FILENAME = "README.md"
VALIDATOR_VERSION = "signal_validation_trust_v1.1"
MARKET_1M_TIMESTAMP_SEMANTICS = "minute_open_utc"
TIMESTAMP_SEMANTICS = "hour_bucket_start_utc"
ENTRY_POLICY = "hour_close_proxy"
FORWARD_SCAN_START_POLICY = "signal_available_at_inclusive"
PRIMARY_LABEL = "+10_before_-8"
PRIMARY_HORIZON_HOURS = 24
SENSITIVITY_TARGETS = (0.05, 0.10, 0.15)
SENSITIVITY_DRAWDOWNS = (0.05, 0.08, 0.12)
HORIZON_TOLERANCE_MINUTES = {
    "1h": 0,
    "4h": 0,
    "24h": 2,
}


@dataclass(frozen=True)
class SignalFamilyDefinition:
    name: str
    title: str
    count_key: str
    candidate_column: str
    grade_column: str | None
    required_columns: Sequence[str]
    grades: Sequence[str] = ()
    emit_gate_flow: bool = False


@dataclass(frozen=True)
class SignalSelector:
    family: SignalFamilyDefinition
    grade: str | None = None

    @property
    def label(self) -> str:
        return f"{self.family.name}_{self.grade}" if self.grade else self.family.name


_COMMON_SIGNAL_COLUMNS = ("exchange", "symbol", "ts")
SIGNAL_FAMILY_REGISTRY: dict[str, SignalFamilyDefinition] = {
    "continuation": SignalFamilyDefinition(
        name="continuation",
        title="Continuation",
        count_key="continuation_count",
        candidate_column="signal_v2_continuation_candidate",
        grade_column="signal_v2_continuation_grade",
        required_columns=(
            *_COMMON_SIGNAL_COLUMNS,
            "signal_v2_continuation_candidate",
            "signal_v2_continuation_grade",
        ),
        grades=("A", "B", "C"),
    ),
    "ignition": SignalFamilyDefinition(
        name="ignition",
        title="Ignition",
        count_key="ignition_count",
        candidate_column="signal_v2_ignition_candidate",
        grade_column="signal_v2_ignition_grade",
        required_columns=(
            *_COMMON_SIGNAL_COLUMNS,
            "signal_v2_ignition_candidate",
            "signal_v2_ignition_grade",
        ),
        grades=("A", "B", "C", "EXTREME"),
    ),
    "reacceleration": SignalFamilyDefinition(
        name="reacceleration",
        title="Reacceleration",
        count_key="reacceleration_count",
        candidate_column="signal_v2_reacceleration_candidate",
        grade_column="signal_v2_reacceleration_grade",
        required_columns=(
            *_COMMON_SIGNAL_COLUMNS,
            "signal_v2_reacceleration_candidate",
            "signal_v2_reacceleration_grade",
        ),
        grades=("A", "B", "C"),
    ),
    "ultra_high_conviction": SignalFamilyDefinition(
        name="ultra_high_conviction",
        title="Ultra High Conviction",
        count_key="ultra_high_conviction_count",
        candidate_column="ultra_high_conviction",
        grade_column=None,
        required_columns=(*_COMMON_SIGNAL_COLUMNS, "ultra_high_conviction"),
        emit_gate_flow=True,
    ),
}

SIGNAL_FAMILY_REQUIRED_FEATURES: dict[str, Sequence[str]] = {
    "continuation": (
        "return_1h_pct",
        "return_4h_pct",
        "return_24h_pct",
        "return_7d_pct",
        "return_30d_pct",
        "return_24h_percentile",
        "return_7d_percentile",
        "return_30d_percentile",
        "relative_strength_score",
        "quality_score",
        "volume_ratio_24h",
        "breakout_20d",
        "continuation_grade",
        "risk_flags",
    ),
    "ignition": (
        "return_1h_pct",
        "return_4h_pct",
        "return_24h_pct",
        "return_24h_rank",
        "return_24h_percentile",
        "relative_strength_score",
        "quality_score",
        "volume_ratio_24h",
        "volume_breakout_score",
        "derivatives_score",
        "ignition_grade",
        "chase_risk_score",
        "risk_flags",
    ),
    "reacceleration": (
        "return_1h_pct",
        "return_4h_pct",
        "return_24h_pct",
        "return_24h_percentile",
        "return_7d_percentile",
        "return_30d_percentile",
        "quality_score",
        "volume_ratio_24h",
        "chase_risk_score",
        "breakout_20d",
        "reacceleration_grade",
    ),
    "ultra_high_conviction": (
        "return_1h_pct",
        "return_4h_pct",
        "return_24h_pct",
        "return_30d_pct",
        "volume_ratio_24h",
        "return_24h_rank",
        "return_24h_percentile",
        "return_7d_percentile",
        "return_30d_percentile",
        "quality_score",
        "breakout_20d",
        "ultra_high_conviction",
    ),
}


def _utc_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _window_slug(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _coerce_utc_datetime(parsed)


def _coerce_utc_timestamp_value(value: datetime | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return pd.Timestamp(value, tz="UTC")
    return timestamp.tz_convert("UTC")


def hour_bucket_start(value: datetime | pd.Timestamp) -> pd.Timestamp:
    return _coerce_utc_timestamp_value(value).floor("h")


def signal_available_at(signal_ts: datetime | pd.Timestamp) -> pd.Timestamp:
    return hour_bucket_start(signal_ts) + pd.Timedelta(hours=1)


def _format_pct_label(value: float) -> str:
    return str(int(round(value * 100)))


def _path_key(target_pct: float, drawdown_pct: float) -> str:
    return f"target_{_format_pct_label(target_pct)}_dd_{_format_pct_label(drawdown_pct)}"


def _empty_forward_rows() -> pd.DataFrame:
    return pd.DataFrame(columns=["ts", "open", "high", "low"])


def _prepare_forward_rows(future_rows: pd.DataFrame, entry_ts: pd.Timestamp, horizon_end: pd.Timestamp) -> pd.DataFrame:
    if future_rows.empty:
        return _empty_forward_rows()
    frame = future_rows.copy()
    for column in ("ts", "open", "high", "low"):
        if column not in frame.columns:
            frame[column] = pd.NA
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True, errors="coerce")
    for column in ("open", "high", "low"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["ts", "high", "low"])
    frame = frame[(frame["ts"] >= entry_ts) & (frame["ts"] < horizon_end)]
    return frame.sort_values("ts").drop_duplicates(subset=["ts"], keep="last").reset_index(drop=True)


def _coverage_for_horizon(rows: pd.DataFrame, entry_ts: pd.Timestamp, horizon: pd.Timedelta, tolerance: int) -> tuple[bool, int, int]:
    expected_minutes = int(horizon.total_seconds() // 60)
    expected = pd.date_range(start=entry_ts, periods=expected_minutes, freq="min", tz="UTC")
    present = set(pd.to_datetime(rows["ts"], utc=True).tolist()) if not rows.empty else set()
    missing_count = sum(1 for ts in expected if ts not in present)
    return missing_count <= tolerance, expected_minutes, missing_count


def _evaluate_target_drawdown_path(
    rows: pd.DataFrame,
    *,
    entry_ts: pd.Timestamp,
    entry_price: float,
    target_pct: float,
    drawdown_pct: float,
) -> dict[str, Any]:
    target_price = round(entry_price * (1.0 + target_pct), 12)
    drawdown_price = round(entry_price * (1.0 - drawdown_pct), 12)
    for row in rows.itertuples(index=False):
        row_ts = _coerce_utc_timestamp_value(row.ts)
        target_hit = float(row.high) >= target_price
        drawdown_hit = float(row.low) <= drawdown_price
        minutes = round((row_ts - entry_ts).total_seconds() / 60.0, 6)
        if target_hit and drawdown_hit:
            return {
                "hit": False,
                "path_order": "ambiguous_same_bar",
                "time_to_hit_minutes": None,
                "time_to_drawdown_minutes": minutes,
            }
        if drawdown_hit:
            return {
                "hit": False,
                "path_order": "drawdown_first",
                "time_to_hit_minutes": None,
                "time_to_drawdown_minutes": minutes,
            }
        if target_hit:
            return {
                "hit": True,
                "path_order": "target_first",
                "time_to_hit_minutes": minutes,
                "time_to_drawdown_minutes": None,
            }
    return {
        "hit": False,
        "path_order": "unresolved",
        "time_to_hit_minutes": None,
        "time_to_drawdown_minutes": None,
    }


def _coerce_valid_entry_price(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0.0 else None


def _conservative_validation_path_labels(
    *,
    signal_start: pd.Timestamp,
    entry_ts: pd.Timestamp,
    horizons: Sequence[pd.Timedelta],
) -> dict[str, Any]:
    labels: dict[str, Any] = {
        "signal_ts": signal_start.isoformat(),
        "signal_available_at": entry_ts.isoformat(),
        "entry_ts": entry_ts.isoformat(),
        "entry_price": None,
        "entry_policy": ENTRY_POLICY,
        "invalid_entry_price": True,
        "label_error": "invalid_entry_price",
        "path_results": {},
        "ambiguous_same_bar": False,
        "path_order": "unresolved",
        "next_minute_open_entry_price": None,
        "next_minute_open_entry_return_delta_pct": None,
        "hit_10pct_before_drawdown_8pct": False,
        "hit_10_before_dd8": False,
        "hit_10pct_first": False,
        "drawdown_8pct_first": False,
        "time_to_hit_10pct_minutes": None,
        "time_to_drawdown_8pct_minutes": None,
        "mfe_before_dd8_pct": 0.0,
        "mae_before_hit_10pct": 0.0,
        "mae_after_hit_10pct": None,
    }
    for horizon in horizons:
        hours = int(horizon.total_seconds() // 3600)
        key = f"{hours}h"
        expected_minutes = int(horizon.total_seconds() // 60)
        labels[f"label_complete_{key}"] = False
        labels[f"expected_minutes_{key}"] = expected_minutes
        labels[f"missing_minutes_{key}"] = expected_minutes
        labels[f"mfe_{key}_pct"] = 0.0
        labels[f"mae_{key}_pct"] = 0.0
        labels[f"abs_mae_{key}_pct"] = 0.0
        labels[f"hit_10pct_{key}"] = False
    for target_pct in SENSITIVITY_TARGETS:
        for drawdown_pct in SENSITIVITY_DRAWDOWNS:
            labels["path_results"][_path_key(target_pct, drawdown_pct)] = {
                "hit": False,
                "path_order": "unresolved",
                "time_to_hit_minutes": None,
                "time_to_drawdown_minutes": None,
            }
    return labels


def _compatibility_path_extremes(
    rows: pd.DataFrame,
    *,
    entry_ts: pd.Timestamp,
    entry_price: float,
    primary_result: dict[str, Any],
) -> dict[str, float | None]:
    if rows.empty:
        return {
            "mfe_before_dd8_pct": 0.0,
            "mae_before_hit_10pct": 0.0,
            "mae_after_hit_10pct": None,
        }

    drawdown_minutes = primary_result.get("time_to_drawdown_minutes")
    if drawdown_minutes is None:
        rows_before_drawdown = rows
    else:
        drawdown_ts = entry_ts + pd.Timedelta(minutes=float(drawdown_minutes))
        rows_before_drawdown = rows[rows["ts"] <= drawdown_ts]
    if rows_before_drawdown.empty:
        mfe_before_dd8 = 0.0
    else:
        mfe_before_dd8 = max((float(rows_before_drawdown["high"].max()) / entry_price - 1.0) * 100.0, 0.0)

    hit_minutes = primary_result.get("time_to_hit_minutes")
    if hit_minutes is None:
        rows_before_hit = rows
        mae_after_hit: float | None = None
    else:
        hit_ts = entry_ts + pd.Timedelta(minutes=float(hit_minutes))
        rows_before_hit = rows[rows["ts"] <= hit_ts]
        rows_after_hit = rows[rows["ts"] > hit_ts]
        if rows_after_hit.empty:
            mae_after_hit = 0.0
        else:
            mae_after_hit = max((1.0 - float(rows_after_hit["low"].min()) / entry_price) * 100.0, 0.0)
    mae_before_hit = max((1.0 - float(rows_before_hit["low"].min()) / entry_price) * 100.0, 0.0) if not rows_before_hit.empty else 0.0
    return {
        "mfe_before_dd8_pct": round(mfe_before_dd8, 6),
        "mae_before_hit_10pct": round(mae_before_hit, 6),
        "mae_after_hit_10pct": round(mae_after_hit, 6) if mae_after_hit is not None else None,
    }


def compute_validation_path_labels(
    *,
    signal_ts: datetime | pd.Timestamp,
    entry_price: float,
    future_rows: pd.DataFrame,
    horizons: Sequence[pd.Timedelta] = (pd.Timedelta(hours=1), pd.Timedelta(hours=4), pd.Timedelta(hours=24)),
) -> dict[str, Any]:
    signal_start = hour_bucket_start(signal_ts)
    entry_ts = signal_available_at(signal_start)
    close = _coerce_valid_entry_price(entry_price)
    if close is None:
        return _conservative_validation_path_labels(
            signal_start=signal_start,
            entry_ts=entry_ts,
            horizons=horizons,
        )

    horizon_end = entry_ts + max(horizons)
    future = _prepare_forward_rows(future_rows, entry_ts, horizon_end)
    labels: dict[str, Any] = {
        "signal_ts": signal_start.isoformat(),
        "signal_available_at": entry_ts.isoformat(),
        "entry_ts": entry_ts.isoformat(),
        "entry_price": close,
        "entry_policy": ENTRY_POLICY,
        "invalid_entry_price": False,
        "label_error": None,
        "path_results": {},
        "ambiguous_same_bar": False,
        "path_order": "unresolved",
        "next_minute_open_entry_price": None,
        "next_minute_open_entry_return_delta_pct": None,
    }
    if not future.empty and "open" in future.columns and pd.notna(future.iloc[0].get("open")):
        next_open = float(future.iloc[0]["open"])
        labels["next_minute_open_entry_price"] = next_open
        labels["next_minute_open_entry_return_delta_pct"] = round((next_open / close - 1.0) * 100.0, 6)

    for horizon in horizons:
        hours = int(horizon.total_seconds() // 3600)
        key = f"{hours}h"
        window_end = entry_ts + horizon
        window_rows = future[future["ts"] < window_end].copy()
        complete, expected_minutes, missing_count = _coverage_for_horizon(
            window_rows,
            entry_ts,
            horizon,
            HORIZON_TOLERANCE_MINUTES.get(key, 0),
        )
        labels[f"label_complete_{key}"] = complete
        labels[f"expected_minutes_{key}"] = expected_minutes
        labels[f"missing_minutes_{key}"] = missing_count
        if window_rows.empty:
            labels[f"mfe_{key}_pct"] = 0.0
            labels[f"mae_{key}_pct"] = 0.0
            labels[f"abs_mae_{key}_pct"] = 0.0
            labels[f"hit_10pct_{key}"] = False
            continue
        high = max(float(window_rows["high"].max()), close)
        low = min(float(window_rows["low"].min()), close)
        labels[f"mfe_{key}_pct"] = round(max((high / close - 1.0) * 100.0, 0.0), 6)
        mae = round(min((low / close - 1.0) * 100.0, 0.0), 6)
        labels[f"mae_{key}_pct"] = mae
        labels[f"abs_mae_{key}_pct"] = round(abs(mae), 6)
        labels[f"hit_10pct_{key}"] = labels[f"mfe_{key}_pct"] >= 10.0

    horizon_rows = future[future["ts"] < entry_ts + pd.Timedelta(hours=24)].copy()
    primary_result: dict[str, Any] | None = None
    for target_pct in SENSITIVITY_TARGETS:
        for drawdown_pct in SENSITIVITY_DRAWDOWNS:
            result = _evaluate_target_drawdown_path(
                horizon_rows,
                entry_ts=entry_ts,
                entry_price=close,
                target_pct=target_pct,
                drawdown_pct=drawdown_pct,
            )
            key = _path_key(target_pct, drawdown_pct)
            labels["path_results"][key] = result
            if target_pct == 0.10 and drawdown_pct == 0.08:
                primary_result = result
                labels["hit_10pct_before_drawdown_8pct"] = bool(result["hit"])
                labels["hit_10_before_dd8"] = bool(result["hit"])
                labels["hit_10pct_first"] = True if result["path_order"] == "target_first" else False
                labels["drawdown_8pct_first"] = result["path_order"] in {"drawdown_first", "ambiguous_same_bar"}
                labels["time_to_hit_10pct_minutes"] = result["time_to_hit_minutes"]
                labels["time_to_drawdown_8pct_minutes"] = result["time_to_drawdown_minutes"]
                labels["path_order"] = result["path_order"]
                labels["ambiguous_same_bar"] = result["path_order"] == "ambiguous_same_bar"

    labels.setdefault("hit_10pct_before_drawdown_8pct", False)
    labels.setdefault("hit_10_before_dd8", False)
    labels.setdefault("hit_10pct_first", False)
    labels.setdefault("drawdown_8pct_first", False)
    labels.setdefault("time_to_hit_10pct_minutes", None)
    labels.setdefault("time_to_drawdown_8pct_minutes", None)
    labels.update(
        _compatibility_path_extremes(
            horizon_rows,
            entry_ts=entry_ts,
            entry_price=close,
            primary_result=primary_result or {},
        )
    )
    return labels


def build_sensitivity_matrix(evaluated: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    matrix: dict[str, dict[str, float | int]] = {}
    complete_rows = [row for row in evaluated if row.get("label_complete_24h")]
    incomplete_count = sum(1 for row in evaluated if not row.get("label_complete_24h"))
    for target_pct in SENSITIVITY_TARGETS:
        for drawdown_pct in SENSITIVITY_DRAWDOWNS:
            key = _path_key(target_pct, drawdown_pct)
            hit_count = sum(1 for row in complete_rows if row.get("path_results", {}).get(key, {}).get("hit") is True)
            eligible_count = len(complete_rows)
            matrix[key] = {
                "eligible_count": eligible_count,
                "hit_count": hit_count,
                "incomplete_count": incomplete_count,
                "precision": round(hit_count / eligible_count, 6) if eligible_count else 0.0,
            }
    return matrix


def default_validation_window(
    window_days: int,
    *,
    now: datetime | pd.Timestamp | None = None,
) -> tuple[datetime, datetime]:
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    current = _coerce_utc_timestamp_value(now or datetime.now(timezone.utc)).floor("h")
    end = current - pd.Timedelta(hours=24)
    start = end - pd.Timedelta(days=window_days)
    return start.to_pydatetime(), end.to_pydatetime()


def parse_signal_selector(raw: str) -> SignalSelector:
    normalized = raw.strip().lower()
    if normalized == "ultra":
        normalized = "ultra_high_conviction"

    family_name = normalized
    grade: str | None = None
    if normalized.endswith("_extreme"):
        family_name = normalized[: -len("_extreme")]
        grade = "EXTREME"
    if len(normalized) > 2 and normalized[-2] == "_" and normalized[-1] in {"a", "b", "c"}:
        family_name = normalized[:-2]
        grade = normalized[-1].upper()

    family = SIGNAL_FAMILY_REGISTRY.get(family_name)
    if family is None or (grade is not None and grade not in family.grades):
        raise ValueError(f"unsupported signal selector: {raw}")
    return SignalSelector(family=family, grade=grade)


def _coerce_signal_selector(value: str | SignalSelector) -> SignalSelector:
    if isinstance(value, SignalSelector):
        return value
    return parse_signal_selector(value)


def _normalize_signal_family(value: str | SignalSelector) -> str:
    return _coerce_signal_selector(value).label


def _signal_family_slug(signal_family: str | SignalSelector) -> str:
    selector = _coerce_signal_selector(signal_family)
    if selector.family.name == "ultra_high_conviction" and selector.grade is None:
        return "ultra"
    return selector.label.replace("_", "-")


def _signal_family_title(signal_family: str | SignalSelector) -> str:
    selector = _coerce_signal_selector(signal_family)
    return f"{selector.family.title} {selector.grade}" if selector.grade else selector.family.title


def _signal_count_key(signal_family: str | SignalSelector) -> str:
    return _coerce_signal_selector(signal_family).family.count_key


def _legacy_signal_count_key(signal_family: str | SignalSelector) -> str:
    selector = _coerce_signal_selector(signal_family)
    prefix = selector.family.name if selector.family.name != "ultra_high_conviction" else "ultra"
    if selector.grade:
        return f"{prefix}_{selector.grade.lower()}_signal_count"
    return f"{prefix}_signal_count"


def _required_features(signal_family: str | SignalSelector) -> list[str]:
    selector = _coerce_signal_selector(signal_family)
    return list(SIGNAL_FAMILY_REQUIRED_FEATURES[selector.family.name])


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _has_veto_reason_codes(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(str(item).strip() for item in value)
    return bool(value)


def _mean_numeric(values: list[Any]) -> float:
    series = pd.to_numeric(pd.Series(values, dtype="object"), errors="coerce").dropna()
    if series.empty:
        return 0.0
    return round(float(series.mean()), 6)


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _median_numeric(values: list[Any]) -> float:
    series = pd.to_numeric(pd.Series(values, dtype="object"), errors="coerce").dropna()
    if series.empty:
        return 0.0
    return round(float(series.median()), 6)


def _optional_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(numeric) else numeric


def _truthy_signal_mask(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False).eq(True)
    normalized = series.fillna("").astype(str).str.strip().str.lower()
    return normalized.isin({"1", "true", "t", "yes", "y"})


_NEGATIVE_GRADE_PLACEHOLDERS = {"", "-", "none", "null", "nan", "false", "0"}


def _has_positive_grade(series: pd.Series) -> pd.Series:
    normalized = series.fillna("").astype(str).str.strip().str.lower()
    return ~normalized.isin(_NEGATIVE_GRADE_PLACEHOLDERS)


def _normalize_legacy_signal_columns(window: pd.DataFrame, selector: SignalSelector) -> pd.DataFrame:
    if selector.family.grade_column is None:
        return window

    legacy_grade_column = f"{selector.family.name}_grade"
    needs_grade = selector.family.grade_column not in window.columns
    needs_candidate = selector.family.candidate_column not in window.columns
    if (not needs_grade and not needs_candidate) or legacy_grade_column not in window.columns:
        return window

    normalized = window.copy()
    if needs_grade:
        normalized[selector.family.grade_column] = normalized[legacy_grade_column]
    if needs_candidate:
        # Legacy production frames only expose grades. Treat a non-placeholder grade
        # as the conservative candidate marker, and keep missing legacy columns hard errors.
        normalized[selector.family.candidate_column] = _has_positive_grade(normalized[selector.family.grade_column])
    return normalized


def _select_signal_rows(window: pd.DataFrame, signal_family: str | SignalSelector) -> pd.DataFrame:
    selector = _coerce_signal_selector(signal_family)
    window = _normalize_legacy_signal_columns(window, selector)
    missing = [column for column in selector.family.required_columns if column not in window.columns]
    if missing:
        columns = ", ".join(missing)
        raise ValueError(f"missing required columns for {selector.label}: {columns}")

    candidate_mask = _truthy_signal_mask(window[selector.family.candidate_column])
    if selector.grade is not None:
        if selector.family.grade_column is None:
            raise ValueError(f"unsupported signal selector: {selector.label}")
        grade_mask = window[selector.family.grade_column].fillna("").astype(str).str.upper().eq(selector.grade)
        candidate_mask &= grade_mask

    selected = window[candidate_mask].copy()
    selected["signal_family"] = selector.family.name
    if selector.grade is not None:
        selected["signal_grade"] = selector.grade
    elif selector.family.grade_column is not None:
        selected["signal_grade"] = selected[selector.family.grade_column].fillna("").astype(str)
    else:
        selected["signal_grade"] = ""
    selected["signal_selector"] = selector.label
    return selected


def summarize_evaluated_signals(
    evaluated: list[dict[str, Any]],
    *,
    signal_family: str = "ultra_high_conviction",
) -> dict[str, Any]:
    signal_count = len(evaluated)
    complete_1h = [row for row in evaluated if row.get("label_complete_1h", True)]
    complete_4h = [row for row in evaluated if row.get("label_complete_4h", True)]
    complete_24h = [row for row in evaluated if row.get("label_complete_24h", True)]
    primary_label_complete_count = len(complete_24h)
    incomplete_label_count = signal_count - primary_label_complete_count

    hit_1h_count = sum(1 for row in complete_1h if row.get("hit_10pct_1h"))
    hit_4h_count = sum(1 for row in complete_4h if row.get("hit_10pct_4h"))
    hit_24h_count = sum(1 for row in complete_24h if row.get("hit_10pct_24h"))
    strict_hit_count = sum(1 for row in complete_24h if row.get("hit_10pct_before_drawdown_8pct"))
    hit_10pct_first_count = sum(1 for row in complete_24h if row.get("hit_10pct_first") is True)
    drawdown_8pct_first_count = sum(1 for row in complete_24h if row.get("drawdown_8pct_first") is True)
    unresolved_24h_count = sum(
        1
        for row in complete_24h
        if row.get("path_order") == "unresolved"
        or (row.get("hit_10pct_first") is None and row.get("drawdown_8pct_first") is None)
    )
    ambiguous_same_bar_count = sum(1 for row in complete_24h if row.get("ambiguous_same_bar") is True)

    selector = _coerce_signal_selector(signal_family)
    count_key = _signal_count_key(selector)
    legacy_count_key = _legacy_signal_count_key(selector)
    count_values = {count_key: signal_count}
    if legacy_count_key != count_key:
        count_values[legacy_count_key] = signal_count
    return {
        "signal_family": selector.label,
        "signal_count": signal_count,
        **count_values,
        "primary_label_complete_count": primary_label_complete_count,
        "incomplete_label_count": incomplete_label_count,
        "hit_10_1h_count": hit_1h_count,
        "hit_10_4h_count": hit_4h_count,
        "hit_10_24h_count": hit_24h_count,
        "hit_10_before_dd8_count": strict_hit_count,
        "hit_10pct_first_count": hit_10pct_first_count,
        "drawdown_8pct_first_count": drawdown_8pct_first_count,
        "unresolved_24h_count": unresolved_24h_count,
        "ambiguous_same_bar_count": ambiguous_same_bar_count,
        "hit10_1h_rate": _rate(hit_1h_count, len(complete_1h)),
        "hit10_4h_rate": _rate(hit_4h_count, len(complete_4h)),
        "hit10_24h_rate": _rate(hit_24h_count, primary_label_complete_count),
        "precision_1h": _rate(hit_1h_count, len(complete_1h)),
        "precision_4h": _rate(hit_4h_count, len(complete_4h)),
        "precision_24h": _rate(hit_24h_count, primary_label_complete_count),
        "precision_before_dd8": _rate(strict_hit_count, primary_label_complete_count),
        "hit_10pct_first_rate": _rate(hit_10pct_first_count, primary_label_complete_count),
        "drawdown_8pct_first_rate": _rate(drawdown_8pct_first_count, primary_label_complete_count),
        "avg_mfe_1h_pct": _mean_numeric([row.get("mfe_1h_pct") for row in complete_1h]),
        "avg_mfe_24h_pct": _mean_numeric([row.get("mfe_24h_pct") for row in complete_24h]),
        "avg_mae_24h_pct": _mean_numeric([row.get("mae_24h_pct") for row in complete_24h]),
        "avg_abs_mae_24h_pct": _mean_numeric([row.get("abs_mae_24h_pct") for row in complete_24h]),
        "avg_mfe_before_dd8_pct": _mean_numeric([row.get("mfe_before_dd8_pct") for row in complete_24h]),
        "avg_mae_before_hit_10pct": _mean_numeric([row.get("mae_before_hit_10pct") for row in complete_24h]),
        "avg_mae_after_hit_10pct": _mean_numeric([row.get("mae_after_hit_10pct") for row in complete_24h]),
        "median_time_to_hit_10pct_minutes": _median_numeric([row.get("time_to_hit_10pct_minutes") for row in complete_24h]),
        "median_time_to_drawdown_8pct_minutes": _median_numeric(
            [row.get("time_to_drawdown_8pct_minutes") for row in complete_24h]
        ),
        "sensitivity_matrix": build_sensitivity_matrix(evaluated),
    }


def summarize_ultra_gate_flow(window: pd.DataFrame) -> dict[str, int]:
    if window.empty:
        return {
            "window_feature_rows": 0,
            "pass_no_veto": 0,
            "pass_20d_breakout": 0,
            "pass_breakout_20d": 0,
            "pass_min_return_1h": 0,
            "pass_max_return_1h": 0,
            "pass_1h_range": 0,
            "pass_min_return_4h": 0,
            "pass_max_return_4h": 0,
            "pass_4h_range": 0,
            "pass_min_return_24h": 0,
            "pass_24h_momentum": 0,
            "pass_min_return_30d": 0,
            "pass_30d_return": 0,
            "pass_min_volume_ratio_24h": 0,
            "pass_max_volume_ratio_24h": 0,
            "pass_volume_ratio_24h_range": 0,
            "pass_rank_24h": 0,
            "pass_top_24h_rank_gate": 0,
            "pass_rs_7d": 0,
            "pass_7d_strength_gate": 0,
            "pass_rs_30d": 0,
            "pass_30d_strength_gate": 0,
            "pass_quality_gate": 0,
            "final_ultra_signal_count": 0,
        }

    rule = ULTRA_HIGH_CONVICTION_RULE
    return_1h_pct = _numeric_series(window, "return_1h_pct")
    return_4h_pct = _numeric_series(window, "return_4h_pct")
    return_24h_pct = _numeric_series(window, "return_24h_pct")
    return_30d_pct = _numeric_series(window, "return_30d_pct")
    volume_ratio_24h = _numeric_series(window, "volume_ratio_24h")
    return_24h_rank = _numeric_series(window, "return_24h_rank")
    return_24h_percentile = _numeric_series(window, "return_24h_percentile")
    return_7d_percentile = _numeric_series(window, "return_7d_percentile")
    return_30d_percentile = _numeric_series(window, "return_30d_percentile")
    quality_score = _numeric_series(window, "quality_score")

    veto_reason_codes = window["veto_reason_codes"].apply(_has_veto_reason_codes) if "veto_reason_codes" in window.columns else pd.Series(False, index=window.index)
    breakout_20d = window["breakout_20d"].fillna(False).astype(bool) if "breakout_20d" in window.columns else pd.Series(False, index=window.index)

    top_24h_rank_gate = (
        (return_24h_rank.notna() & return_24h_rank.le(rule.max_return_24h_rank))
        | (return_24h_rank.isna() & return_24h_percentile.ge(rule.min_return_24h_percentile))
    )

    pass_no_veto = ~veto_reason_codes
    pass_20d_breakout = pass_no_veto & breakout_20d
    pass_min_return_1h = pass_20d_breakout & return_1h_pct.ge(rule.min_return_1h_pct)
    pass_max_return_1h = pass_min_return_1h & return_1h_pct.le(rule.max_return_1h_pct)
    pass_min_return_4h = pass_max_return_1h & return_4h_pct.ge(rule.min_return_4h_pct)
    pass_max_return_4h = pass_min_return_4h & return_4h_pct.le(rule.max_return_4h_pct)
    pass_min_return_24h = pass_max_return_4h & return_24h_pct.ge(rule.min_return_24h_pct)
    pass_min_return_30d = pass_min_return_24h & return_30d_pct.ge(rule.min_return_30d_pct)
    pass_min_volume_ratio_24h = pass_min_return_30d & volume_ratio_24h.ge(rule.min_volume_ratio_24h)
    pass_max_volume_ratio_24h = pass_min_volume_ratio_24h & volume_ratio_24h.le(rule.max_volume_ratio_24h)
    pass_rank_24h = pass_max_volume_ratio_24h & top_24h_rank_gate
    pass_rs_7d = pass_rank_24h & return_7d_percentile.ge(rule.min_return_7d_percentile)
    pass_rs_30d = pass_rs_7d & return_30d_percentile.ge(rule.min_return_30d_percentile)
    pass_quality_gate = pass_rs_30d & quality_score.ge(rule.min_quality_score)

    return {
        "window_feature_rows": int(len(window)),
        "pass_no_veto": int(pass_no_veto.sum()),
        "pass_20d_breakout": int(pass_20d_breakout.sum()),
        "pass_breakout_20d": int(pass_20d_breakout.sum()),
        "pass_min_return_1h": int(pass_min_return_1h.sum()),
        "pass_max_return_1h": int(pass_max_return_1h.sum()),
        "pass_1h_range": int(pass_max_return_1h.sum()),
        "pass_min_return_4h": int(pass_min_return_4h.sum()),
        "pass_max_return_4h": int(pass_max_return_4h.sum()),
        "pass_4h_range": int(pass_max_return_4h.sum()),
        "pass_min_return_24h": int(pass_min_return_24h.sum()),
        "pass_24h_momentum": int(pass_min_return_24h.sum()),
        "pass_min_return_30d": int(pass_min_return_30d.sum()),
        "pass_30d_return": int(pass_min_return_30d.sum()),
        "pass_min_volume_ratio_24h": int(pass_min_volume_ratio_24h.sum()),
        "pass_max_volume_ratio_24h": int(pass_max_volume_ratio_24h.sum()),
        "pass_volume_ratio_24h_range": int(pass_max_volume_ratio_24h.sum()),
        "pass_rank_24h": int(pass_rank_24h.sum()),
        "pass_top_24h_rank_gate": int(pass_rank_24h.sum()),
        "pass_rs_7d": int(pass_rs_7d.sum()),
        "pass_7d_strength_gate": int(pass_rs_7d.sum()),
        "pass_rs_30d": int(pass_rs_30d.sum()),
        "pass_30d_strength_gate": int(pass_rs_30d.sum()),
        "pass_quality_gate": int(pass_quality_gate.sum()),
        "final_ultra_signal_count": int(pass_quality_gate.sum()),
    }


def fetch_hourly_bars(engine: Engine, exchange: str, start: datetime, end: datetime) -> pd.DataFrame:
    statement = text(
        """
        SELECT
            m.asset_id,
            m.exchange,
            m.symbol,
            date_trunc('hour', m.ts) AS ts,
            (array_agg(m.open ORDER BY m.ts ASC))[1] AS open,
            max(m.high) AS high,
            min(m.low) AS low,
            (array_agg(m.close ORDER BY m.ts DESC))[1] AS close,
            sum(m.volume) AS volume,
            sum(m.quote_volume) AS quote_volume,
            sum(m.trade_count) AS trade_count
        FROM alt_core.market_1m AS m
        WHERE m.exchange = :exchange
          AND m.ts >= :start
          AND m.ts < :end
        GROUP BY m.asset_id, m.exchange, m.symbol, date_trunc('hour', m.ts)
        ORDER BY m.asset_id, ts
        """
    )
    with engine.begin() as connection:
        rows = connection.execute(statement, {"exchange": exchange, "start": start, "end": end}).mappings().all()
    return pd.DataFrame(rows)


def fetch_forward_1m_rows(engine: Engine, asset_id: int, signal_ts: datetime | pd.Timestamp, horizon: timedelta) -> pd.DataFrame:
    signal_available = signal_available_at(signal_ts).to_pydatetime()
    horizon_end = (pd.Timestamp(signal_available) + pd.Timedelta(horizon)).to_pydatetime()
    statement = text(
        """
        SELECT
            m.ts,
            m.open,
            m.high,
            m.low
        FROM alt_core.market_1m AS m
        WHERE m.asset_id = :asset_id
          AND m.ts >= :signal_available_at
          AND m.ts < :horizon_end
        ORDER BY m.ts
        """
    )
    with engine.begin() as connection:
        rows = connection.execute(
            statement,
            {"asset_id": asset_id, "signal_available_at": signal_available, "horizon_end": horizon_end},
        ).mappings().all()
    return pd.DataFrame(rows)


def evaluate_signal_family(
    engine: Engine,
    exchange: str,
    start: datetime,
    end: datetime,
    *,
    signal_family: str = "ultra_high_conviction",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    signal_family = _normalize_signal_family(signal_family)
    start_utc = _coerce_utc_datetime(start)
    end_utc = _coerce_utc_datetime(end)
    if start_utc >= end_utc:
        raise ValueError("start must be earlier than end")

    market_start = start_utc - timedelta(days=31)
    market_end = end_utc + timedelta(hours=25)
    hourly = fetch_hourly_bars(engine, exchange=exchange, start=market_start, end=market_end)
    if hourly.empty:
        return {
            "signal_family": signal_family,
            "exchange": exchange,
            "from": start_utc.isoformat(),
            "to": end_utc.isoformat(),
            "hourly_rows": 0,
            "feature_rows": 0,
            "gate_flow": summarize_ultra_gate_flow(pd.DataFrame()) if signal_family == "ultra_high_conviction" else {},
            "group_summary": summarize_signal_v2_groups(pd.DataFrame()),
            "sensitivity_matrix": build_sensitivity_matrix([]),
            **summarize_evaluated_signals([], signal_family=signal_family),
        }, []

    features = _prepare_feature_frame(hourly)
    window = features[(features["ts"] >= pd.Timestamp(start_utc)) & (features["ts"] < pd.Timestamp(end_utc))].copy()
    gate_flow = summarize_ultra_gate_flow(window) if signal_family == "ultra_high_conviction" else {}
    signals = _select_signal_rows(window, signal_family)

    evaluated: list[dict[str, Any]] = []
    for row in signals.sort_values(["ts", "symbol"]).to_dict("records"):
        signal_ts = hour_bucket_start(pd.Timestamp(row["ts"]))
        future_1m = fetch_forward_1m_rows(engine, int(row["asset_id"]), signal_ts, timedelta(hours=24))
        labels = compute_validation_path_labels(
            signal_ts=signal_ts,
            entry_price=float(row["close"]),
            future_rows=future_1m,
        )
        evaluated.append(
            {
                "ts": signal_ts.isoformat(),
                "signal_ts": labels["signal_ts"],
                "signal_available_at": labels["signal_available_at"],
                "entry_ts": labels["entry_ts"],
                "entry_price": labels["entry_price"],
                "entry_policy": labels["entry_policy"],
                "invalid_entry_price": bool(labels.get("invalid_entry_price", False)),
                "label_error": labels.get("label_error"),
                "asset_id": int(row["asset_id"]),
                "exchange": row["exchange"],
                "symbol": row["symbol"],
                "close": float(row["close"]),
                "continuation_grade": row.get("continuation_grade"),
                "ignition_grade": row.get("ignition_grade"),
                "reacceleration_grade": row.get("reacceleration_grade"),
                "ultra_high_conviction": bool(row.get("ultra_high_conviction", False)),
                "signal_priority": int(row["signal_priority"]) if _optional_float(row.get("signal_priority")) is not None else None,
                "return_1h_pct": float(row["return_1h_pct"]),
                "return_4h_pct": float(row["return_4h_pct"]),
                "return_24h_pct": float(row["return_24h_pct"]),
                "return_7d_pct": float(row["return_7d_pct"]),
                "return_30d_pct": float(row["return_30d_pct"]),
                "volume_ratio_24h": float(row["volume_ratio_24h"]),
                "volume_breakout_score": _optional_float(row.get("volume_breakout_score")),
                "relative_strength_score": _optional_float(row.get("relative_strength_score")),
                "derivatives_score": _optional_float(row.get("derivatives_score")),
                "quality_score": _optional_float(row.get("quality_score")),
                "chase_risk_score": _optional_float(row.get("chase_risk_score")),
                "return_24h_rank": _optional_float(row.get("return_24h_rank")),
                "return_24h_percentile": _optional_float(row.get("return_24h_percentile")),
                "return_7d_percentile": _optional_float(row.get("return_7d_percentile")),
                "return_30d_percentile": _optional_float(row.get("return_30d_percentile")),
                "risk_flags": list(row.get("risk_flags", ())),
                "mfe_1h_pct": labels["mfe_1h_pct"],
                "mfe_4h_pct": labels["mfe_4h_pct"],
                "mfe_24h_pct": labels["mfe_24h_pct"],
                "mae_1h_pct": labels["mae_1h_pct"],
                "mae_4h_pct": labels["mae_4h_pct"],
                "mae_24h_pct": labels["mae_24h_pct"],
                "label_complete_1h": labels["label_complete_1h"],
                "label_complete_4h": labels["label_complete_4h"],
                "label_complete_24h": labels["label_complete_24h"],
                "expected_minutes_1h": labels["expected_minutes_1h"],
                "expected_minutes_4h": labels["expected_minutes_4h"],
                "expected_minutes_24h": labels["expected_minutes_24h"],
                "missing_minutes_1h": labels["missing_minutes_1h"],
                "missing_minutes_4h": labels["missing_minutes_4h"],
                "missing_minutes_24h": labels["missing_minutes_24h"],
                "abs_mae_1h_pct": labels["abs_mae_1h_pct"],
                "abs_mae_4h_pct": labels["abs_mae_4h_pct"],
                "abs_mae_24h_pct": labels["abs_mae_24h_pct"],
                "mfe_before_dd8_pct": labels["mfe_before_dd8_pct"],
                "mae_before_hit_10pct": labels["mae_before_hit_10pct"],
                "mae_after_hit_10pct": labels["mae_after_hit_10pct"],
                "hit_10pct_1h": labels["hit_10pct_1h"],
                "hit_10pct_4h": labels["hit_10pct_4h"],
                "hit_10pct_24h": labels["hit_10pct_24h"],
                "hit_10pct_before_drawdown_8pct": bool(labels["hit_10pct_before_drawdown_8pct"]),
                "hit_10_before_dd8": bool(labels["hit_10_before_dd8"]),
                "hit_10pct_first": labels["hit_10pct_first"],
                "drawdown_8pct_first": labels["drawdown_8pct_first"],
                "time_to_hit_10pct_minutes": labels["time_to_hit_10pct_minutes"],
                "time_to_drawdown_8pct_minutes": labels["time_to_drawdown_8pct_minutes"],
                "path_order": labels["path_order"],
                "ambiguous_same_bar": labels["ambiguous_same_bar"],
                "path_results": labels["path_results"],
                "path_results_json": json.dumps(labels["path_results"], sort_keys=True),
                "next_minute_open_entry_price": labels["next_minute_open_entry_price"],
                "next_minute_open_entry_return_delta_pct": labels["next_minute_open_entry_return_delta_pct"],
            }
        )

    evaluated_frame = pd.DataFrame(evaluated)
    summary = {
        "signal_family": signal_family,
        "exchange": exchange,
        "from": start_utc.isoformat(),
        "to": end_utc.isoformat(),
        "market_from": market_start.isoformat(),
        "market_to": market_end.isoformat(),
        "hourly_rows": int(len(hourly)),
        "feature_rows": int(len(features)),
        "gate_flow": gate_flow,
        "group_summary": summarize_signal_v2_groups(evaluated_frame),
        "sensitivity_matrix": build_sensitivity_matrix(evaluated),
        **summarize_evaluated_signals(evaluated, signal_family=signal_family),
    }
    return summary, evaluated


def evaluate_ultra_signals(
    engine: Engine,
    exchange: str,
    start: datetime,
    end: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return evaluate_signal_family(
        engine,
        exchange,
        start,
        end,
        signal_family="ultra_high_conviction",
    )


def build_run_metadata(
    *,
    exchange: str,
    start: datetime,
    end: datetime,
    market_start: datetime,
    market_end: datetime,
    output_dir: Path,
    output_root: Path,
    signal_family: str = "ultra_high_conviction",
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    signal_family = _normalize_signal_family(signal_family)
    generated = _coerce_utc_datetime(generated_at or datetime.now(timezone.utc))
    return {
        "script": "scripts/validate_ultra_signal_production.py",
        "generated_at": generated.isoformat(),
        "signal_family": signal_family,
        "signal_family_slug": _signal_family_slug(signal_family),
        "signal_family_title": _signal_family_title(signal_family),
        "exchange": exchange,
        "validation_window": {
            "from": start.isoformat(),
            "to": end.isoformat(),
        },
        "warmup_window": {
            "from": market_start.isoformat(),
            "to": start.isoformat(),
        },
        "forward_window": {
            "from": end.isoformat(),
            "to": market_end.isoformat(),
            "horizon": "24h",
        },
        "expected_inputs": {
            "database_tables": ["alt_core.market_1m"],
            "required_features": _required_features(signal_family),
        },
        "expected_outputs": {
            "summary": SUMMARY_FILENAME,
            "signals": SIGNALS_FILENAME,
            "metadata": METADATA_FILENAME,
            "readme": README_FILENAME,
            "signal_identity_columns": ["exchange", "symbol", "ts", "asset_id"],
            "path_risk_fields": [
                "hit_10pct_before_drawdown_8pct",
                "time_to_hit_10pct_minutes",
                "time_to_drawdown_8pct_minutes",
                "mfe_before_dd8_pct",
                "mae_before_hit_10pct",
                "mae_after_hit_10pct",
                "hit_10pct_first",
                "drawdown_8pct_first",
            ],
        },
        "artifacts": {
            "output_root": str(output_root),
            "output_dir": str(output_dir),
        },
        "metrics": {
            "precision_1h": f"share of {_signal_family_title(signal_family)} rows hitting +10% MFE within 1h",
            "precision_4h": f"share of {_signal_family_title(signal_family)} rows hitting +10% MFE within 4h",
            "precision_24h": f"share of {_signal_family_title(signal_family)} rows hitting +10% MFE within 24h",
            "precision_before_dd8": "share hitting +10% before any -8% drawdown",
            "avg_mfe_before_dd8_pct": "average max favorable excursion before the first -8% drawdown, or full 24h MFE if no -8% drawdown occurs",
            "avg_mae_before_hit_10pct": "average max adverse excursion before the first +10% hit, or full 24h MAE if +10% is never reached",
            "avg_mae_after_hit_10pct": "average max adverse excursion after the first +10% hit and before the 24h horizon ends",
            "median_time_to_drawdown_8pct_minutes": "median minutes from signal to the first -8% drawdown event within 24h",
        },
    }


def _build_group_snapshot_lines(summary: dict[str, Any], signal_family: str) -> list[str]:
    group_summary = summary.get("group_summary", {})
    if not isinstance(group_summary, dict):
        return []

    lines: list[str] = []
    if signal_family == "ignition":
        for key in ("ignition_EXTREME", "ignition_A", "ignition_B", "high_chase_risk", "low_or_medium_chase_risk"):
            group = group_summary.get(key)
            if not isinstance(group, dict):
                continue
            lines.append(
                f"- {key}: count={int(group.get('signal_count', 0))}, "
                f"hit_10_before_dd8={float(group.get('hit_10pct_before_drawdown_8pct_rate', 0.0)):.6f}, "
                f"avg_mae_24h_pct={float(group.get('avg_mae_24h_pct', 0.0)):.6f}"
            )
    elif signal_family == "ultra_high_conviction":
        group = group_summary.get("ultra_high_conviction")
        if isinstance(group, dict):
            lines.append(
                f"- ultra_high_conviction: count={int(group.get('signal_count', 0))}, "
                f"hit_10_before_dd8={float(group.get('hit_10pct_before_drawdown_8pct_rate', 0.0)):.6f}, "
                f"avg_mae_24h_pct={float(group.get('avg_mae_24h_pct', 0.0)):.6f}"
            )
    return lines


def build_run_readme(summary: dict[str, Any], metadata: dict[str, Any]) -> str:
    window = metadata["validation_window"]
    warmup = metadata["warmup_window"]
    forward = metadata["forward_window"]
    outputs = metadata["expected_outputs"]
    signal_family = _normalize_signal_family(str(metadata.get("signal_family", "ultra_high_conviction")))
    count_key = _signal_count_key(signal_family)
    legacy_count_key = _legacy_signal_count_key(signal_family)
    count_value = summary.get(count_key, summary.get("signal_count", 0))
    lines = [
        f"# {_signal_family_title(signal_family)} Production Validation",
        "",
        f"- generated_at: {metadata['generated_at']}",
        f"- signal_family: {signal_family}",
        f"- exchange: {metadata['exchange']}",
        f"- validation_window: {window['from']} -> {window['to']}",
        f"- warmup_window: {warmup['from']} -> {warmup['to']}",
        f"- forward_window: {forward['from']} -> {forward['to']} ({forward['horizon']})",
        "",
        "## Expected Inputs",
        "",
        "- database table: alt_core.market_1m",
        f"- feature fields: {', '.join(metadata['expected_inputs']['required_features'])}",
        "",
        "## Outputs",
        "",
        f"- {outputs['summary']}: aggregate hit-rate and drawdown summary",
        f"- {outputs['signals']}: per-signal evaluation rows",
        f"- {outputs['metadata']}: reproducibility manifest for this run",
        f"- {outputs['readme']}: human-readable run contract",
        "",
        "## Snapshot",
        "",
        f"- {count_key}: {count_value}",
    ]
    if legacy_count_key != count_key:
        lines.append(f"- {legacy_count_key}: {summary.get(legacy_count_key, count_value)}")
    lines.extend(
        [
            f"- signal_count: {summary.get('signal_count', count_value)}",
            f"- precision_1h: {summary['precision_1h']}",
            f"- precision_4h: {summary['precision_4h']}",
            f"- precision_24h: {summary['precision_24h']}",
            f"- precision_before_dd8: {summary['precision_before_dd8']}",
            f"- hit_10pct_first_rate: {summary['hit_10pct_first_rate']}",
            f"- drawdown_8pct_first_rate: {summary['drawdown_8pct_first_rate']}",
            f"- avg_mfe_24h_pct: {summary['avg_mfe_24h_pct']}",
            f"- avg_mae_24h_pct: {summary['avg_mae_24h_pct']}",
            f"- avg_mfe_before_dd8_pct: {summary['avg_mfe_before_dd8_pct']}",
            f"- avg_mae_before_hit_10pct: {summary['avg_mae_before_hit_10pct']}",
            f"- avg_mae_after_hit_10pct: {summary['avg_mae_after_hit_10pct']}",
            f"- median_time_to_hit_10pct_minutes: {summary['median_time_to_hit_10pct_minutes']}",
            f"- median_time_to_drawdown_8pct_minutes: {summary['median_time_to_drawdown_8pct_minutes']}",
            "",
        ]
    )
    group_snapshot_lines = _build_group_snapshot_lines(summary, signal_family)
    if group_snapshot_lines:
        lines.extend(["## Group Snapshot", "", *group_snapshot_lines, ""])
    gate_flow = summary.get("gate_flow", {})
    if gate_flow:
        lines.extend(
            [
                "## Gate Flow",
                "",
                f"- window_feature_rows: {gate_flow.get('window_feature_rows', 0)}",
                f"- pass_20d_breakout: {gate_flow.get('pass_20d_breakout', 0)}",
                f"- pass_min_return_1h: {gate_flow.get('pass_min_return_1h', 0)}",
                f"- pass_max_return_1h: {gate_flow.get('pass_max_return_1h', 0)}",
                f"- pass_min_return_4h: {gate_flow.get('pass_min_return_4h', 0)}",
                f"- pass_max_return_4h: {gate_flow.get('pass_max_return_4h', 0)}",
                f"- pass_min_return_24h: {gate_flow.get('pass_min_return_24h', 0)}",
                f"- pass_rank_24h: {gate_flow.get('pass_rank_24h', 0)}",
                f"- pass_rs_7d: {gate_flow.get('pass_rs_7d', 0)}",
                f"- pass_rs_30d: {gate_flow.get('pass_rs_30d', 0)}",
                f"- pass_quality_gate: {gate_flow.get('pass_quality_gate', 0)}",
                f"- final_ultra_signal_count: {gate_flow.get('final_ultra_signal_count', 0)}",
                "",
            ]
        )
    return "\n".join(lines)


def write_artifacts(output_dir: Path, summary: dict[str, Any], rows: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / SUMMARY_FILENAME).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / METADATA_FILENAME).write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / README_FILENAME).write_text(build_run_readme(summary, metadata), encoding="utf-8")
    if not rows:
        (output_dir / SIGNALS_FILENAME).write_text("", encoding="utf-8")
        return
    with (output_dir / SIGNALS_FILENAME).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", required=True)
    parser.add_argument("--to", dest="end", required=True)
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--signal-family", default="ultra_high_conviction")
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()

    settings = load_settings()
    engine = build_engine(settings)
    start = _parse_datetime(args.start)
    end = _parse_datetime(args.end)
    signal_family = _normalize_signal_family(args.signal_family)
    summary, rows = evaluate_signal_family(engine, args.exchange, start, end, signal_family=signal_family)
    output_root = Path(args.output_root)
    output_dir = output_root / (
        f"{_utc_slug()}-production-{_signal_family_slug(signal_family)}-{args.exchange}-{_window_slug(start)}-{_window_slug(end)}"
    )
    metadata = build_run_metadata(
        exchange=args.exchange,
        start=start,
        end=end,
        market_start=_parse_datetime(summary["market_from"]),
        market_end=_parse_datetime(summary["market_to"]),
        output_dir=output_dir,
        output_root=output_root,
        signal_family=signal_family,
    )
    write_artifacts(output_dir, summary, rows, metadata)
    print(f"output_dir={output_dir}")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
