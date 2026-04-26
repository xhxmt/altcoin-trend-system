from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import text

from altcoin_trend.config import AppSettings
from altcoin_trend.db import insert_market_rows_ignore_conflicts, upsert_instruments
from altcoin_trend.ingest.bootstrap import BootstrapAdapter, filter_instruments
from altcoin_trend.ingest.derivatives import _asset_ids_for_exchange, _to_epoch_ms, _update_market_1m_derivative
from altcoin_trend.ingest.normalize import market_bar_to_row
from altcoin_trend.models import Instrument


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketSyncResult:
    exchange: str
    instruments_selected: int
    bars_written: int
    failed_symbols: int = 0


@dataclass(frozen=True)
class DerivativesSyncResult:
    exchange: str
    instruments_selected: int
    updates_written: int


def _latest_market_timestamps(engine, asset_ids: list[int]) -> dict[int, datetime]:
    if not asset_ids:
        return {}
    statement = text(
        """
        SELECT asset_id, MAX(ts) AS latest_ts
        FROM alt_core.market_1m
        WHERE asset_id = ANY(:asset_ids)
        GROUP BY asset_id
        """
    )
    with engine.begin() as connection:
        return {
            int(row["asset_id"]): row["latest_ts"]
            for row in connection.execute(statement, {"asset_ids": asset_ids}).mappings()
            if row["latest_ts"] is not None
        }


def _latest_derivative_timestamps(engine, asset_ids: list[int]) -> dict[int, datetime]:
    if not asset_ids:
        return {}
    statement = text(
        """
        SELECT asset_id, MAX(ts) AS latest_ts
        FROM alt_core.market_1m
        WHERE asset_id = ANY(:asset_ids)
          AND (
              open_interest IS NOT NULL
              OR funding_rate IS NOT NULL
              OR long_short_ratio IS NOT NULL
              OR buy_sell_ratio IS NOT NULL
          )
        GROUP BY asset_id
        """
    )
    with engine.begin() as connection:
        return {
            int(row["asset_id"]): row["latest_ts"]
            for row in connection.execute(statement, {"asset_ids": asset_ids}).mappings()
            if row["latest_ts"] is not None
        }


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def sync_exchange_market_data(
    *,
    adapter: BootstrapAdapter,
    engine,
    settings: AppSettings,
    now: datetime,
    instruments: list[Instrument] | None = None,
    fallback_lookback_minutes: int = 180,
) -> MarketSyncResult:
    current_time = _utc(now)
    selected_instruments = filter_instruments(
        instruments if instruments is not None else adapter.fetch_instruments(),
        settings=settings,
        now=current_time,
    )
    asset_ids_by_symbol = upsert_instruments(engine, selected_instruments)
    latest_by_asset = _latest_market_timestamps(engine, [int(asset_id) for asset_id in asset_ids_by_symbol.values()])
    bars_written = 0
    failed_symbols = 0

    for instrument in selected_instruments:
        asset_id = int(asset_ids_by_symbol[instrument.symbol])
        latest_ts = latest_by_asset.get(asset_id)
        if latest_ts is None:
            start = current_time - timedelta(minutes=fallback_lookback_minutes)
        else:
            start = _utc(latest_ts) + timedelta(minutes=1)
        if start >= current_time:
            continue

        try:
            bars = adapter.fetch_klines_1m(instrument.symbol, _to_epoch_ms(start), _to_epoch_ms(current_time))
        except Exception:
            failed_symbols += 1
            logger.warning(
                "Market data fetch failed exchange=%s symbol=%s start=%s end=%s",
                adapter.exchange,
                instrument.symbol,
                start.isoformat(),
                current_time.isoformat(),
                exc_info=True,
            )
            continue
        rows = [market_bar_to_row(asset_id, bar) for bar in bars if bar.is_closed]
        bars_written += insert_market_rows_ignore_conflicts(engine, rows)

    return MarketSyncResult(
        exchange=adapter.exchange,
        instruments_selected=len(selected_instruments),
        bars_written=bars_written,
        failed_symbols=failed_symbols,
    )


def sync_exchange_derivatives(
    *,
    adapter,
    engine,
    settings: AppSettings,
    now: datetime,
    instruments: list[Instrument] | None = None,
    fallback_lookback_hours: int = 24,
    min_refresh_minutes: int = 55,
) -> DerivativesSyncResult:
    current_time = _utc(now)
    selected_instruments = filter_instruments(
        instruments if instruments is not None else adapter.fetch_instruments(),
        settings=settings,
        now=current_time,
    )
    asset_ids_by_symbol = _asset_ids_for_exchange(engine, adapter.exchange)
    selected_asset_ids = [
        int(asset_ids_by_symbol[instrument.symbol])
        for instrument in selected_instruments
        if instrument.symbol in asset_ids_by_symbol
    ]
    latest_by_asset = _latest_derivative_timestamps(engine, selected_asset_ids)
    updates = 0
    due_ranges: list[tuple[object, int, datetime]] = []

    for instrument in selected_instruments:
        asset_id = asset_ids_by_symbol.get(instrument.symbol)
        if asset_id is None:
            continue
        latest_ts = latest_by_asset.get(int(asset_id))
        if latest_ts is None:
            start = current_time - timedelta(hours=fallback_lookback_hours)
        else:
            latest_utc = _utc(latest_ts)
            if current_time - latest_utc < timedelta(minutes=min_refresh_minutes):
                continue
            start = latest_utc + timedelta(minutes=1)
        if start < current_time:
            due_ranges.append((instrument, int(asset_id), start))

    if not due_ranges:
        return DerivativesSyncResult(
            exchange=adapter.exchange,
            instruments_selected=len(selected_instruments),
            updates_written=0,
        )

    with engine.begin() as connection:
        for instrument, asset_id, start in due_ranges:
            start_ms = _to_epoch_ms(start)
            end_ms = _to_epoch_ms(current_time)
            if hasattr(adapter, "fetch_open_interest_history"):
                for observation in adapter.fetch_open_interest_history(instrument.symbol, start_ms, end_ms, "1h"):
                    updates += _update_market_1m_derivative(
                        connection,
                        asset_id,
                        observation.ts,
                        {"open_interest": observation.open_interest},
                    )
            if hasattr(adapter, "fetch_funding_rate_history"):
                for observation in adapter.fetch_funding_rate_history(instrument.symbol, start_ms, end_ms):
                    updates += _update_market_1m_derivative(
                        connection,
                        asset_id,
                        observation.ts,
                        {"funding_rate": observation.funding_rate},
                    )
            if hasattr(adapter, "fetch_long_short_ratio_history"):
                for observation in adapter.fetch_long_short_ratio_history(instrument.symbol, start_ms, end_ms, "1h"):
                    updates += _update_market_1m_derivative(
                        connection,
                        asset_id,
                        observation.ts,
                        {"long_short_ratio": observation.long_short_ratio},
                    )

    return DerivativesSyncResult(
        exchange=adapter.exchange,
        instruments_selected=len(selected_instruments),
        updates_written=updates,
    )
