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
from altcoin_trend.trade_backtest import _coerce_utc_datetime, _prepare_feature_frame, compute_forward_path_labels


def _utc_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _coerce_utc_datetime(parsed)


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
            "ultra_signal_count": 0,
            "hit_10_1h_count": 0,
            "hit_10_4h_count": 0,
            "hit_10_24h_count": 0,
            "hit_10_before_dd8_count": 0,
            "precision_1h": 0.0,
            "precision_4h": 0.0,
            "precision_24h": 0.0,
            "precision_before_dd8": 0.0,
        }, []

    features = _prepare_feature_frame(hourly)
    window = features[(features["ts"] >= pd.Timestamp(start_utc)) & (features["ts"] < pd.Timestamp(end_utc))].copy()
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
                "hit_10pct_1h": labels["mfe_1h_pct"] >= 10.0,
                "hit_10pct_4h": labels["mfe_4h_pct"] >= 10.0,
                "hit_10pct_24h": labels["mfe_24h_pct"] >= 10.0,
                "hit_10pct_before_drawdown_8pct": bool(labels["hit_10pct_before_drawdown_8pct"]),
                "time_to_hit_10pct_minutes": labels["time_to_hit_10pct_minutes"],
            }
        )

    signal_count = len(evaluated)
    hit_1h_count = sum(1 for row in evaluated if row["hit_10pct_1h"])
    hit_4h_count = sum(1 for row in evaluated if row["hit_10pct_4h"])
    hit_24h_count = sum(1 for row in evaluated if row["hit_10pct_24h"])
    strict_hit_count = sum(1 for row in evaluated if row["hit_10pct_before_drawdown_8pct"])
    summary = {
        "exchange": exchange,
        "from": start_utc.isoformat(),
        "to": end_utc.isoformat(),
        "market_from": market_start.isoformat(),
        "market_to": market_end.isoformat(),
        "hourly_rows": int(len(hourly)),
        "feature_rows": int(len(features)),
        "ultra_signal_count": signal_count,
        "hit_10_1h_count": hit_1h_count,
        "hit_10_4h_count": hit_4h_count,
        "hit_10_24h_count": hit_24h_count,
        "hit_10_before_dd8_count": strict_hit_count,
        "precision_1h": round(hit_1h_count / signal_count, 6) if signal_count else 0.0,
        "precision_4h": round(hit_4h_count / signal_count, 6) if signal_count else 0.0,
        "precision_24h": round(hit_24h_count / signal_count, 6) if signal_count else 0.0,
        "precision_before_dd8": round(strict_hit_count / signal_count, 6) if signal_count else 0.0,
        "avg_mfe_1h_pct": round(float(pd.Series([row["mfe_1h_pct"] for row in evaluated]).mean()), 6) if evaluated else 0.0,
        "avg_mfe_24h_pct": round(float(pd.Series([row["mfe_24h_pct"] for row in evaluated]).mean()), 6) if evaluated else 0.0,
        "avg_mae_24h_pct": round(float(pd.Series([row["mae_24h_pct"] for row in evaluated]).mean()), 6) if evaluated else 0.0,
    }
    return summary, evaluated


def write_artifacts(output_dir: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not rows:
        (output_dir / "signals.csv").write_text("", encoding="utf-8")
        return
    with (output_dir / "signals.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", required=True)
    parser.add_argument("--to", dest="end", required=True)
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--output-root", default="artifacts/autoresearch")
    args = parser.parse_args()

    settings = load_settings()
    engine = build_engine(settings)
    start = _parse_datetime(args.start)
    end = _parse_datetime(args.end)
    summary, rows = evaluate_ultra_signals(engine, args.exchange, start, end)
    output_dir = Path(args.output_root) / f"{_utc_slug()}-production-ultra-{args.exchange}"
    write_artifacts(output_dir, summary, rows)
    print(f"output_dir={output_dir}")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
