from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Engine, text

from altcoin_trend.config import load_settings
from altcoin_trend.db import build_engine
from altcoin_trend.signals.trade_candidate import ULTRA_HIGH_CONVICTION_RULE
from altcoin_trend.trade_backtest import _coerce_utc_datetime, _prepare_feature_frame, compute_forward_path_labels

DEFAULT_OUTPUT_ROOT = "artifacts/autoresearch"
SUMMARY_FILENAME = "summary.json"
SIGNALS_FILENAME = "signals.csv"
METADATA_FILENAME = "metadata.json"
README_FILENAME = "README.md"


def _utc_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _window_slug(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _coerce_utc_datetime(parsed)


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


def _median_numeric(values: list[Any]) -> float:
    series = pd.to_numeric(pd.Series(values, dtype="object"), errors="coerce").dropna()
    if series.empty:
        return 0.0
    return round(float(series.median()), 6)


def summarize_evaluated_signals(evaluated: list[dict[str, Any]]) -> dict[str, Any]:
    signal_count = len(evaluated)
    hit_1h_count = sum(1 for row in evaluated if row.get("hit_10pct_1h"))
    hit_4h_count = sum(1 for row in evaluated if row.get("hit_10pct_4h"))
    hit_24h_count = sum(1 for row in evaluated if row.get("hit_10pct_24h"))
    strict_hit_count = sum(1 for row in evaluated if row.get("hit_10pct_before_drawdown_8pct"))
    hit_10pct_first_count = sum(1 for row in evaluated if row.get("hit_10pct_first") is True)
    drawdown_8pct_first_count = sum(1 for row in evaluated if row.get("drawdown_8pct_first") is True)
    unresolved_24h_count = sum(
        1
        for row in evaluated
        if row.get("hit_10pct_first") is None and row.get("drawdown_8pct_first") is None
    )

    return {
        "ultra_signal_count": signal_count,
        "hit_10_1h_count": hit_1h_count,
        "hit_10_4h_count": hit_4h_count,
        "hit_10_24h_count": hit_24h_count,
        "hit_10_before_dd8_count": strict_hit_count,
        "hit_10pct_first_count": hit_10pct_first_count,
        "drawdown_8pct_first_count": drawdown_8pct_first_count,
        "unresolved_24h_count": unresolved_24h_count,
        "precision_1h": round(hit_1h_count / signal_count, 6) if signal_count else 0.0,
        "precision_4h": round(hit_4h_count / signal_count, 6) if signal_count else 0.0,
        "precision_24h": round(hit_24h_count / signal_count, 6) if signal_count else 0.0,
        "precision_before_dd8": round(strict_hit_count / signal_count, 6) if signal_count else 0.0,
        "hit_10pct_first_rate": round(hit_10pct_first_count / signal_count, 6) if signal_count else 0.0,
        "drawdown_8pct_first_rate": round(drawdown_8pct_first_count / signal_count, 6) if signal_count else 0.0,
        "avg_mfe_1h_pct": _mean_numeric([row.get("mfe_1h_pct") for row in evaluated]),
        "avg_mfe_24h_pct": _mean_numeric([row.get("mfe_24h_pct") for row in evaluated]),
        "avg_mae_24h_pct": _mean_numeric([row.get("mae_24h_pct") for row in evaluated]),
        "avg_mfe_before_dd8_pct": _mean_numeric([row.get("mfe_before_dd8_pct") for row in evaluated]),
        "avg_mae_before_hit_10pct": _mean_numeric([row.get("mae_before_hit_10pct") for row in evaluated]),
        "avg_mae_after_hit_10pct": _mean_numeric([row.get("mae_after_hit_10pct") for row in evaluated]),
        "median_time_to_hit_10pct_minutes": _median_numeric([row.get("time_to_hit_10pct_minutes") for row in evaluated]),
        "median_time_to_drawdown_8pct_minutes": _median_numeric([row.get("time_to_drawdown_8pct_minutes") for row in evaluated]),
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
            max(m.ts) AS ts,
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


def fetch_forward_1m_rows(engine: Engine, asset_id: int, signal_ts: datetime, horizon: timedelta) -> pd.DataFrame:
    statement = text(
        """
        SELECT
            m.ts,
            m.high,
            m.low
        FROM alt_core.market_1m AS m
        WHERE m.asset_id = :asset_id
          AND m.ts > :signal_ts
          AND m.ts <= :horizon_end
        ORDER BY m.ts
        """
    )
    horizon_end = signal_ts + horizon
    with engine.begin() as connection:
        rows = connection.execute(
            statement,
            {"asset_id": asset_id, "signal_ts": signal_ts, "horizon_end": horizon_end},
        ).mappings().all()
    return pd.DataFrame(rows)


def evaluate_ultra_signals(
    engine: Engine,
    exchange: str,
    start: datetime,
    end: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    start_utc = _coerce_utc_datetime(start)
    end_utc = _coerce_utc_datetime(end)
    if start_utc >= end_utc:
        raise ValueError("start must be earlier than end")

    market_start = start_utc - timedelta(days=31)
    market_end = end_utc + timedelta(hours=25)
    hourly = fetch_hourly_bars(engine, exchange=exchange, start=market_start, end=market_end)
    if hourly.empty:
        return {
            "exchange": exchange,
            "from": start_utc.isoformat(),
            "to": end_utc.isoformat(),
            "hourly_rows": 0,
            "feature_rows": 0,
            "gate_flow": summarize_ultra_gate_flow(pd.DataFrame()),
            **summarize_evaluated_signals([]),
        }, []

    features = _prepare_feature_frame(hourly)
    window = features[(features["ts"] >= pd.Timestamp(start_utc)) & (features["ts"] < pd.Timestamp(end_utc))].copy()
    gate_flow = summarize_ultra_gate_flow(window)
    signals = window[window["ultra_high_conviction"].fillna(False).eq(True)].copy()

    evaluated: list[dict[str, Any]] = []
    for row in signals.sort_values(["ts", "symbol"]).to_dict("records"):
        signal_ts = _coerce_utc_datetime(pd.Timestamp(row["ts"]).to_pydatetime())
        future_1m = fetch_forward_1m_rows(engine, int(row["asset_id"]), signal_ts, timedelta(hours=24))
        labels = compute_forward_path_labels(pd.Timestamp(signal_ts), float(row["close"]), future_1m)
        evaluated.append(
            {
                "ts": signal_ts.isoformat(),
                "asset_id": int(row["asset_id"]),
                "exchange": row["exchange"],
                "symbol": row["symbol"],
                "close": float(row["close"]),
                "return_1h_pct": float(row["return_1h_pct"]),
                "return_4h_pct": float(row["return_4h_pct"]),
                "return_24h_pct": float(row["return_24h_pct"]),
                "return_7d_pct": float(row["return_7d_pct"]),
                "return_30d_pct": float(row["return_30d_pct"]),
                "volume_ratio_24h": float(row["volume_ratio_24h"]),
                "return_24h_percentile": float(row["return_24h_percentile"]),
                "return_7d_percentile": float(row["return_7d_percentile"]),
                "return_30d_percentile": float(row["return_30d_percentile"]),
                "mfe_1h_pct": labels["mfe_1h_pct"],
                "mfe_4h_pct": labels["mfe_4h_pct"],
                "mfe_24h_pct": labels["mfe_24h_pct"],
                "mae_1h_pct": labels["mae_1h_pct"],
                "mae_4h_pct": labels["mae_4h_pct"],
                "mae_24h_pct": labels["mae_24h_pct"],
                "mfe_before_dd8_pct": labels["mfe_before_dd8_pct"],
                "mae_before_hit_10pct": labels["mae_before_hit_10pct"],
                "mae_after_hit_10pct": labels["mae_after_hit_10pct"],
                "hit_10pct_1h": labels["mfe_1h_pct"] >= 10.0,
                "hit_10pct_4h": labels["mfe_4h_pct"] >= 10.0,
                "hit_10pct_24h": labels["mfe_24h_pct"] >= 10.0,
                "hit_10pct_before_drawdown_8pct": bool(labels["hit_10pct_before_drawdown_8pct"]),
                "hit_10pct_first": labels["hit_10pct_first"],
                "drawdown_8pct_first": labels["drawdown_8pct_first"],
                "time_to_hit_10pct_minutes": labels["time_to_hit_10pct_minutes"],
                "time_to_drawdown_8pct_minutes": labels["time_to_drawdown_8pct_minutes"],
            }
        )

    summary = {
        "exchange": exchange,
        "from": start_utc.isoformat(),
        "to": end_utc.isoformat(),
        "market_from": market_start.isoformat(),
        "market_to": market_end.isoformat(),
        "hourly_rows": int(len(hourly)),
        "feature_rows": int(len(features)),
        "gate_flow": gate_flow,
        **summarize_evaluated_signals(evaluated),
    }
    return summary, evaluated


def build_run_metadata(
    *,
    exchange: str,
    start: datetime,
    end: datetime,
    market_start: datetime,
    market_end: datetime,
    output_dir: Path,
    output_root: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = _coerce_utc_datetime(generated_at or datetime.now(timezone.utc))
    return {
        "script": "scripts/validate_ultra_signal_production.py",
        "generated_at": generated.isoformat(),
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
            "required_features": [
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
            ],
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
            "precision_1h": "share of ultra_high_conviction rows hitting +10% MFE within 1h",
            "precision_4h": "share of ultra_high_conviction rows hitting +10% MFE within 4h",
            "precision_24h": "share of ultra_high_conviction rows hitting +10% MFE within 24h",
            "precision_before_dd8": "share hitting +10% before any -8% drawdown",
            "avg_mfe_before_dd8_pct": "average max favorable excursion before the first -8% drawdown, or full 24h MFE if no -8% drawdown occurs",
            "avg_mae_before_hit_10pct": "average max adverse excursion before the first +10% hit, or full 24h MAE if +10% is never reached",
            "avg_mae_after_hit_10pct": "average max adverse excursion after the first +10% hit and before the 24h horizon ends",
            "median_time_to_drawdown_8pct_minutes": "median minutes from signal to the first -8% drawdown event within 24h",
        },
    }


def build_run_readme(summary: dict[str, Any], metadata: dict[str, Any]) -> str:
    window = metadata["validation_window"]
    warmup = metadata["warmup_window"]
    forward = metadata["forward_window"]
    outputs = metadata["expected_outputs"]
    return "\n".join(
        [
            "# Ultra High Conviction Production Validation",
            "",
            f"- generated_at: {metadata['generated_at']}",
            f"- exchange: {metadata['exchange']}",
            f"- validation_window: {window['from']} -> {window['to']}",
            f"- warmup_window: {warmup['from']} -> {warmup['to']}",
            f"- forward_window: {forward['from']} -> {forward['to']} ({forward['horizon']})",
            "",
            "## Expected Inputs",
            "",
            "- database table: alt_core.market_1m",
            "- feature fields: return_1h_pct, return_4h_pct, return_24h_pct, return_30d_pct, volume_ratio_24h, return_24h_rank, return_24h_percentile, return_7d_percentile, return_30d_percentile, quality_score, breakout_20d, ultra_high_conviction",
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
            f"- ultra_signal_count: {summary['ultra_signal_count']}",
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
            "## Gate Flow",
            "",
            f"- window_feature_rows: {summary.get('gate_flow', {}).get('window_feature_rows', 0)}",
            f"- pass_20d_breakout: {summary.get('gate_flow', {}).get('pass_20d_breakout', 0)}",
            f"- pass_min_return_1h: {summary.get('gate_flow', {}).get('pass_min_return_1h', 0)}",
            f"- pass_max_return_1h: {summary.get('gate_flow', {}).get('pass_max_return_1h', 0)}",
            f"- pass_min_return_4h: {summary.get('gate_flow', {}).get('pass_min_return_4h', 0)}",
            f"- pass_max_return_4h: {summary.get('gate_flow', {}).get('pass_max_return_4h', 0)}",
            f"- pass_min_return_24h: {summary.get('gate_flow', {}).get('pass_min_return_24h', 0)}",
            f"- pass_rank_24h: {summary.get('gate_flow', {}).get('pass_rank_24h', 0)}",
            f"- pass_rs_7d: {summary.get('gate_flow', {}).get('pass_rs_7d', 0)}",
            f"- pass_rs_30d: {summary.get('gate_flow', {}).get('pass_rs_30d', 0)}",
            f"- pass_quality_gate: {summary.get('gate_flow', {}).get('pass_quality_gate', 0)}",
            f"- final_ultra_signal_count: {summary.get('gate_flow', {}).get('final_ultra_signal_count', 0)}",
            "",
        ]
    )


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
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()

    settings = load_settings()
    engine = build_engine(settings)
    start = _parse_datetime(args.start)
    end = _parse_datetime(args.end)
    summary, rows = evaluate_ultra_signals(engine, args.exchange, start, end)
    output_root = Path(args.output_root)
    output_dir = output_root / (
        f"{_utc_slug()}-production-ultra-{args.exchange}-{_window_slug(start)}-{_window_slug(end)}"
    )
    metadata = build_run_metadata(
        exchange=args.exchange,
        start=start,
        end=end,
        market_start=_parse_datetime(summary["market_from"]),
        market_end=_parse_datetime(summary["market_to"]),
        output_dir=output_dir,
        output_root=output_root,
    )
    write_artifacts(output_dir, summary, rows, metadata)
    print(f"output_dir={output_dir}")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
