from __future__ import annotations

from datetime import datetime, timezone

from altcoin_trend.config import AppSettings
from altcoin_trend.models import Instrument


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
