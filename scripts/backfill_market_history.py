from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import text

from altcoin_trend.config import load_settings
from altcoin_trend.db import build_engine, insert_market_rows_ignore_conflicts, upsert_instruments
from altcoin_trend.exchanges.binance import BinancePublicAdapter
from altcoin_trend.exchanges.bybit import BybitPublicAdapter
from altcoin_trend.ingest.bootstrap import filter_instruments
from altcoin_trend.ingest.normalize import market_bar_to_row


def _adapter_for(exchange: str):
    if exchange == "binance":
        return BinancePublicAdapter()
    if exchange == "bybit":
        return BybitPublicAdapter()
    raise ValueError(f"Unsupported exchange: {exchange}")


def _to_epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _existing_range(engine, asset_id: int) -> tuple[datetime | None, datetime | None]:
    statement = text(
        """
        SELECT min(ts) AS min_ts, max(ts) AS max_ts
        FROM alt_core.market_1m
        WHERE asset_id = :asset_id
        """
    )
    with engine.begin() as connection:
        row = connection.execute(statement, {"asset_id": asset_id}).mappings().one()
    min_ts = _coerce_utc(row["min_ts"]) if row["min_ts"] is not None else None
    max_ts = _coerce_utc(row["max_ts"]) if row["max_ts"] is not None else None
    return min_ts, max_ts


def _existing_chunk_count(engine, asset_id: int, start: datetime, end: datetime) -> int:
    statement = text(
        """
        SELECT count(*) AS rows
        FROM alt_core.market_1m
        WHERE asset_id = :asset_id
          AND ts >= :start
          AND ts < :end
        """
    )
    with engine.begin() as connection:
        return int(connection.execute(statement, {"asset_id": asset_id, "start": start, "end": end}).scalar_one())


def _expected_chunk_minutes(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() // 60))


def _chunk_is_complete(engine, asset_id: int, start: datetime, end: datetime) -> bool:
    expected = _expected_chunk_minutes(start, end)
    if expected <= 0:
        return True
    existing = _existing_chunk_count(engine, asset_id, start, end)
    return existing >= max(1, expected - 2)


def _chunk_interval(start: datetime, end: datetime, chunk_days: int) -> Iterable[tuple[datetime, datetime]]:
    cursor = start
    step = timedelta(days=chunk_days)
    while cursor < end:
        chunk_end = min(cursor + step, end)
        yield cursor, chunk_end
        cursor = chunk_end


def _fetch_with_retries(adapter, symbol: str, start: datetime, end: datetime, retries: int):
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return adapter.fetch_klines_1m(symbol, _to_epoch_ms(start), _to_epoch_ms(end))
        except Exception as exc:  # noqa: BLE001 - operational script should retry transport/provider failures.
            last_error = exc
            sleep_seconds = min(30, 2 * attempt)
            print(
                f"fetch_error exchange={adapter.exchange} symbol={symbol} "
                f"start={start.isoformat()} end={end.isoformat()} attempt={attempt}/{retries} "
                f"error={exc}",
                flush=True,
            )
            time.sleep(sleep_seconds)
    assert last_error is not None
    raise last_error


def backfill_exchange(engine, exchange: str, lookback_days: int, chunk_days: int, retries: int) -> int:
    settings = load_settings()
    adapter = _adapter_for(exchange)
    now = datetime.now(timezone.utc)
    target_start = now - timedelta(days=lookback_days)
    target_end = now

    instruments = filter_instruments(adapter.fetch_instruments(), settings=settings, now=now)
    asset_ids = upsert_instruments(engine, instruments)

    print(
        f"exchange_start exchange={exchange} instruments={len(instruments)} "
        f"target_start={target_start.isoformat()} target_end={target_end.isoformat()}",
        flush=True,
    )

    total_written = 0
    for index, instrument in enumerate(instruments, start=1):
        asset_id = asset_ids[instrument.symbol]
        existing_min, existing_max = _existing_range(engine, asset_id)
        symbol_written = 0
        print(
            f"symbol_start exchange={exchange} symbol={instrument.symbol} index={index}/{len(instruments)} "
            f"existing_min={existing_min.isoformat() if existing_min else None} "
            f"existing_max={existing_max.isoformat() if existing_max else None}",
            flush=True,
        )
        for chunk_start, chunk_end in _chunk_interval(target_start, target_end, chunk_days):
            if _chunk_is_complete(engine, asset_id, chunk_start, chunk_end):
                print(
                    f"chunk_skip exchange={exchange} symbol={instrument.symbol} "
                    f"start={chunk_start.isoformat()} end={chunk_end.isoformat()}",
                    flush=True,
                )
                continue
            bars = _fetch_with_retries(adapter, instrument.symbol, chunk_start, chunk_end, retries)
            rows = [market_bar_to_row(asset_id, bar) for bar in bars if bar.is_closed]
            written = insert_market_rows_ignore_conflicts(engine, rows)
            symbol_written += written
            total_written += written
            print(
                f"chunk_done exchange={exchange} symbol={instrument.symbol} "
                f"start={chunk_start.isoformat()} end={chunk_end.isoformat()} "
                f"fetched={len(rows)} inserted={written} total_inserted={total_written}",
                flush=True,
            )
        print(
            f"symbol_done exchange={exchange} symbol={instrument.symbol} inserted={symbol_written}",
            flush=True,
        )

    print(f"exchange_done exchange={exchange} inserted={total_written}", flush=True)
    return total_written


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill market_1m data in bounded chunks.")
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--chunk-days", type=int, default=7)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--exchange", action="append", choices=("binance", "bybit"))
    args = parser.parse_args()

    if args.lookback_days < 1:
        raise ValueError("--lookback-days must be >= 1")
    if args.chunk_days < 1:
        raise ValueError("--chunk-days must be >= 1")
    if args.retries < 1:
        raise ValueError("--retries must be >= 1")

    settings = load_settings()
    engine = build_engine(settings)
    exchanges = tuple(args.exchange or settings.exchanges)

    total = 0
    for exchange in exchanges:
        total += backfill_exchange(
            engine=engine,
            exchange=exchange,
            lookback_days=args.lookback_days,
            chunk_days=args.chunk_days,
            retries=args.retries,
        )
    print(f"backfill_done exchanges={','.join(exchanges)} inserted={total}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
