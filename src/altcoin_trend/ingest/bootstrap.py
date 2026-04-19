from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from altcoin_trend.config import AppSettings
from altcoin_trend.db import insert_market_rows_ignore_conflicts, upsert_instruments
from altcoin_trend.ingest.normalize import market_bar_to_row
from altcoin_trend.models import Instrument


class BootstrapAdapter(Protocol):
    exchange: str

    def fetch_instruments(self) -> list[Instrument]:
        ...

    def fetch_klines_1m(self, symbol: str, start_ms: int, end_ms: int):
        ...


@dataclass(frozen=True)
class BootstrapResult:
    exchange: str
    instruments_selected: int
    bars_written: int


def _listing_age_days(onboard_at: datetime, now: datetime) -> float:
    if onboard_at.tzinfo is None:
        onboard_at = onboard_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - onboard_at).total_seconds() / 86400


def filter_instruments(instruments: list[Instrument], settings: AppSettings, now: datetime) -> list[Instrument]:
    selected: list[Instrument] = []
    allowlist = settings.allowlist_symbols
    blocklist = settings.blocklist_symbols

    for instrument in instruments:
        if instrument.quote_asset != settings.quote_asset:
            continue
        if instrument.market_type != "usdt_perp":
            continue
        if instrument.status != "trading":
            continue
        if instrument.symbol in blocklist:
            continue
        if allowlist and instrument.symbol not in allowlist:
            continue
        if instrument.onboard_at is not None and _listing_age_days(instrument.onboard_at, now) < settings.min_listing_days:
            continue
        selected.append(instrument)

    return selected


def _to_epoch_ms(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def bootstrap_exchange(
    adapter: BootstrapAdapter,
    engine,
    settings: AppSettings,
    lookback_days: int,
    now: datetime,
) -> BootstrapResult:
    if lookback_days < 1:
        raise ValueError("lookback_days must be >= 1")

    instruments = filter_instruments(adapter.fetch_instruments(), settings=settings, now=now)
    asset_ids = upsert_instruments(engine, instruments)
    start_ms = _to_epoch_ms(now) - lookback_days * 86_400_000
    end_ms = _to_epoch_ms(now)
    bars_written = 0

    for instrument in instruments:
        asset_id = asset_ids[instrument.symbol]
        bars = adapter.fetch_klines_1m(instrument.symbol, start_ms, end_ms)
        rows = [market_bar_to_row(asset_id, bar) for bar in bars if bar.is_closed]
        bars_written += insert_market_rows_ignore_conflicts(engine, rows)

    return BootstrapResult(
        exchange=adapter.exchange,
        instruments_selected=len(instruments),
        bars_written=bars_written,
    )
