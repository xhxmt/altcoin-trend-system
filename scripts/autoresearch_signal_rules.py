from __future__ import annotations

import argparse
import csv
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pandas as pd


BINANCE_FAPI = "https://fapi.binance.com"


@dataclass(frozen=True)
class RuleConfig:
    name: str
    min_return_1h: float
    min_return_4h: float
    max_return_4h: float
    min_return_24h: float
    min_return_30d: float
    min_volume_ratio: float
    max_volume_ratio: float
    min_rs_percentile_24h: float
    min_rs_percentile_7d: float
    min_rs_percentile_30d: float
    require_20d_breakout: bool


def _utc_now_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _clean_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def fetch_top_symbols(client: httpx.Client, top: int) -> list[str]:
    exchange_info = client.get(f"{BINANCE_FAPI}/fapi/v1/exchangeInfo").json()
    ticker_rows = client.get(f"{BINANCE_FAPI}/fapi/v1/ticker/24hr").json()

    eligible = {
        _clean_symbol(item["symbol"])
        for item in exchange_info.get("symbols", [])
        if item.get("quoteAsset") == "USDT"
        and item.get("contractType") == "PERPETUAL"
        and item.get("status") == "TRADING"
    }
    ranked: list[tuple[str, float]] = []
    for row in ticker_rows:
        symbol = _clean_symbol(str(row.get("symbol", "")))
        if symbol not in eligible:
            continue
        try:
            ranked.append((symbol, float(row.get("quoteVolume", 0.0))))
        except (TypeError, ValueError):
            continue
    ranked.sort(key=lambda item: item[1], reverse=True)
    return [symbol for symbol, _ in ranked[:top]]


def fetch_klines_1h(client: httpx.Client, symbol: str, days: int) -> list[dict[str, Any]]:
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - days * 86_400_000
    response = client.get(
        f"{BINANCE_FAPI}/fapi/v1/klines",
        params={
            "symbol": symbol,
            "interval": "1h",
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": min(1500, days * 24 + 24),
        },
    )
    response.raise_for_status()
    rows = response.json()
    parsed: list[dict[str, Any]] = []
    for row in rows:
        try:
            parsed.append(
                {
                    "symbol": symbol,
                    "ts": pd.to_datetime(int(row[0]), unit="ms", utc=True),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                    "quote_volume": float(row[7]),
                }
            )
        except (TypeError, ValueError, IndexError):
            continue
    return parsed


def build_feature_frame(raw_rows: list[dict[str, Any]], evaluation_days: int) -> pd.DataFrame:
    frame = pd.DataFrame(raw_rows)
    if frame.empty:
        return frame
    frame = frame.sort_values(["symbol", "ts"]).reset_index(drop=True)
    grouped = frame.groupby("symbol", group_keys=False)

    frame["return_1h"] = grouped["close"].pct_change(1)
    frame["return_4h"] = grouped["close"].pct_change(4)
    frame["return_24h"] = grouped["close"].pct_change(24)
    frame["return_7d"] = grouped["close"].pct_change(24 * 7)
    frame["return_30d"] = grouped["close"].pct_change(24 * 30)
    rolling_volume = grouped["quote_volume"].rolling(24, min_periods=12).mean().reset_index(level=0, drop=True)
    frame["volume_ratio_24h"] = frame["quote_volume"] / rolling_volume
    rolling_high_20d = grouped["high"].rolling(24 * 20, min_periods=24 * 7).max().reset_index(level=0, drop=True)
    frame["breakout_20d"] = frame["close"] > rolling_high_20d.shift(1)
    frame["future_high_1h"] = grouped["high"].shift(-1)
    frame["future_max_return_1h"] = (frame["future_high_1h"] / frame["close"]) - 1.0

    for column in ("return_24h", "return_7d", "return_30d", "volume_ratio_24h"):
        frame[f"{column}_pctile"] = frame.groupby("ts")[column].rank(pct=True)

    max_ts = frame["ts"].max()
    start_ts = max_ts - pd.Timedelta(days=evaluation_days)
    frame = frame[frame["ts"] >= start_ts].copy()
    required = [
        "return_1h",
        "return_4h",
        "return_24h",
        "return_7d",
        "return_30d",
        "volume_ratio_24h",
        "return_24h_pctile",
        "return_7d_pctile",
        "future_max_return_1h",
    ]
    return frame.dropna(subset=required).reset_index(drop=True)


def candidate_rules(iterations: int) -> list[RuleConfig]:
    seeds = [
        (0.01, 0.02, 0.0, 0.03, 0.0, 1.5, 0.0, 0.70, 0.60, 0.00, False),
        (0.02, 0.03, 0.0, 0.04, 0.0, 2.0, 0.0, 0.75, 0.65, 0.00, False),
        (0.03, 0.05, 0.0, 0.06, 0.0, 2.5, 0.0, 0.80, 0.70, 0.00, False),
        (0.04, 0.07, 0.0, 0.08, 0.0, 3.0, 0.0, 0.85, 0.75, 0.00, False),
        (0.05, 0.09, 0.0, 0.10, 0.0, 4.0, 0.0, 0.90, 0.80, 0.00, False),
        (0.02, 0.04, 0.0, 0.05, 0.0, 3.0, 0.0, 0.85, 0.80, 0.00, True),
        (0.03, 0.06, 0.0, 0.08, 0.0, 2.0, 0.0, 0.90, 0.85, 0.00, True),
        (0.01, 0.03, 0.0, 0.06, 0.0, 4.0, 0.0, 0.95, 0.90, 0.00, False),
        (0.04, 0.04, 0.0, 0.04, 0.0, 5.0, 0.0, 0.75, 0.70, 0.00, True),
        (0.00, 0.08, 0.0, 0.12, 0.0, 2.5, 0.0, 0.95, 0.85, 0.00, True),
        (0.06, 0.08, 0.0, 0.12, 0.0, 5.5, 0.0, 0.97, 0.97, 0.80, True),
        (0.07, 0.08, 0.0, 0.50, 0.0, 5.0, 10.0, 0.97, 0.98, 0.80, True),
        (0.18, 0.08, 0.0, 0.50, 0.0, 5.0, 10.0, 0.97, 0.98, 0.80, True),
        (0.12, 0.38, 0.0, 0.50, 0.0, 5.0, 10.0, 0.97, 0.98, 0.80, True),
        (0.12, 0.38, 1.10, 0.50, 0.65, 5.0, 10.0, 0.999, 0.98, 0.80, True),
    ]
    rules: list[RuleConfig] = []
    for index in range(iterations):
        base = seeds[index % len(seeds)]
        cycle = index // len(seeds)
        rules.append(
            RuleConfig(
                name=f"iter_{index + 1:02d}",
                min_return_1h=round(base[0] + cycle * 0.005, 4),
                min_return_4h=round(base[1] + cycle * 0.005, 4),
                max_return_4h=base[2],
                min_return_24h=round(base[3] + cycle * 0.01, 4),
                min_return_30d=base[4],
                min_volume_ratio=round(base[5] + cycle * 0.5, 4),
                max_volume_ratio=base[6],
                min_rs_percentile_24h=round(min(1.0, base[7] + cycle * 0.02), 4),
                min_rs_percentile_7d=round(min(0.99, base[8] + cycle * 0.02), 4),
                min_rs_percentile_30d=round(min(0.99, base[9] + cycle * 0.02), 4),
                require_20d_breakout=bool(base[10]),
            )
        )
    return rules


def evaluate_rule(frame: pd.DataFrame, rule: RuleConfig, target_return: float) -> dict[str, Any]:
    mask = (
        (frame["return_1h"] >= rule.min_return_1h)
        & (frame["return_4h"] >= rule.min_return_4h)
        & (frame["return_24h"] >= rule.min_return_24h)
        & (frame["volume_ratio_24h"] >= rule.min_volume_ratio)
        & (frame["return_24h_pctile"] >= rule.min_rs_percentile_24h)
        & (frame["return_7d_pctile"] >= rule.min_rs_percentile_7d)
        & (frame["return_30d_pctile"] >= rule.min_rs_percentile_30d)
    )
    if rule.require_20d_breakout:
        mask &= frame["breakout_20d"]
    if rule.max_return_4h > 0:
        mask &= frame["return_4h"] <= rule.max_return_4h
    if rule.min_return_30d > 0:
        mask &= frame["return_30d"] >= rule.min_return_30d
    if rule.max_volume_ratio > 0:
        mask &= frame["volume_ratio_24h"] <= rule.max_volume_ratio

    signals = frame[mask].copy()
    signal_count = int(len(signals))
    if signal_count == 0:
        precision = 0.0
        avg_return = 0.0
        best_return = 0.0
        median_return = 0.0
        hit_count = 0
    else:
        hit_count = int((signals["future_max_return_1h"] >= target_return).sum())
        precision = hit_count / signal_count
        avg_return = float(signals["future_max_return_1h"].mean())
        best_return = float(signals["future_max_return_1h"].max())
        median_return = float(signals["future_max_return_1h"].median())

    score = precision * 100.0 + min(signal_count, 200) / 20.0 + avg_return * 100.0
    if signal_count < 5:
        score -= 20.0
    return {
        **asdict(rule),
        "signals": signal_count,
        "hits": hit_count,
        "precision": precision,
        "avg_future_max_return": avg_return,
        "median_future_max_return": median_return,
        "best_future_max_return": best_return,
        "score": score,
    }


def choose_best(rows: list[dict[str, Any]]) -> dict[str, Any]:
    viable = [row for row in rows if row["signals"] >= 5]
    pool = viable if viable else rows
    return max(
        pool,
        key=lambda row: (
            row["precision"],
            row["avg_future_max_return"],
            row["hits"],
            -row["signals"],
            row["score"],
        ),
    )


def write_summary(output_dir: Path, frame: pd.DataFrame, results: list[dict[str, Any]], best: dict[str, Any]) -> None:
    positives = int((frame["future_max_return_1h"] >= 0.10).sum())
    summary = [
        "# Signal Rule Autoresearch",
        "",
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"Evaluation rows: {len(frame)}",
        f"Symbols: {frame['symbol'].nunique() if not frame.empty else 0}",
        f"Raw 1h +10% opportunities: {positives}",
        "",
        "## Best Rule",
        "",
    ]
    for key, value in best.items():
        summary.append(f"- {key}: {value}")
    summary.extend(
        [
            "",
            "## Notes",
            "",
            "- Data source: Binance USDT perpetual public REST klines.",
            "- Feature interval: 1h bars; this is a research approximation before production 1m integration.",
            "- Target: next 1h candle high reaches at least +10% from signal close.",
        ]
    )
    (output_dir / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--evaluation-days", type=int, default=30)
    parser.add_argument("--lookback-days", type=int, default=60)
    parser.add_argument("--top", type=int, default=120)
    parser.add_argument("--target-return", type=float, default=0.10)
    parser.add_argument("--output-root", default="artifacts/autoresearch")
    args = parser.parse_args()

    output_dir = Path(args.output_root) / f"{_utc_now_slug()}-signal-rules"
    output_dir.mkdir(parents=True, exist_ok=True)
    latest_link = Path(args.output_root) / "latest-signal-rules"
    if latest_link.exists() or latest_link.is_symlink():
        latest_link.unlink()
    latest_link.symlink_to(output_dir.resolve())

    print(f"output_dir={output_dir}", flush=True)
    all_rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=30) as client:
        symbols = fetch_top_symbols(client, args.top)
        print(f"symbols={len(symbols)}", flush=True)
        for index, symbol in enumerate(symbols, start=1):
            try:
                all_rows.extend(fetch_klines_1h(client, symbol, args.lookback_days))
            except Exception as exc:
                print(f"fetch_error symbol={symbol} error={exc}", flush=True)
            if index % 25 == 0:
                print(f"fetched={index}/{len(symbols)} rows={len(all_rows)}", flush=True)
                time.sleep(0.5)

    frame = build_feature_frame(all_rows, args.evaluation_days)
    frame.to_csv(output_dir / "features.csv.gz", index=False, compression="gzip")
    print(f"evaluation_rows={len(frame)} symbols={frame['symbol'].nunique() if not frame.empty else 0}", flush=True)

    results: list[dict[str, Any]] = []
    for rule in candidate_rules(args.iterations):
        result = evaluate_rule(frame, rule, args.target_return)
        results.append(result)
        print(
            "iteration={name} signals={signals} hits={hits} precision={precision:.4f} "
            "avg_ret={avg_future_max_return:.4f} best_ret={best_future_max_return:.4f}".format(**result),
            flush=True,
        )

    best = choose_best(results)
    with (output_dir / "signal-rule-iterations.tsv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0].keys()), delimiter="\t")
        writer.writeheader()
        writer.writerows(results)
    (output_dir / "best_rule.json").write_text(json.dumps(best, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_summary(output_dir, frame, results, best)
    print(f"best={json.dumps(best, sort_keys=True)}", flush=True)
    return 0 if math.isfinite(float(best["score"])) else 1


if __name__ == "__main__":
    raise SystemExit(main())
