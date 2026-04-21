from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import time

from altcoin_trend.config import AppSettings, load_settings
from altcoin_trend.db import build_engine
from altcoin_trend.exchanges.binance import BinancePublicAdapter
from altcoin_trend.exchanges.bybit import BybitPublicAdapter
from altcoin_trend.ingest.incremental import sync_exchange_derivatives, sync_exchange_market_data
from altcoin_trend.models import Instrument
from altcoin_trend.scheduler import process_alerts, run_once_pipeline
from altcoin_trend.signals.telegram import TelegramClient


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InputSyncResult:
    status: str
    message: str


@dataclass
class InstrumentCache:
    ttl_seconds: int
    _entries: dict[str, tuple[datetime, list[Instrument]]] = field(default_factory=dict)

    def get(self, adapter, now: datetime) -> list[Instrument]:
        loaded = self._entries.get(adapter.exchange)
        if loaded is not None:
            loaded_at, instruments = loaded
            if now - loaded_at < timedelta(seconds=self.ttl_seconds):
                return instruments

        instruments = adapter.fetch_instruments()
        self._entries[adapter.exchange] = (now, instruments)
        return instruments


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _adapter_for_exchange(exchange: str):
    if exchange == "binance":
        return BinancePublicAdapter()
    if exchange == "bybit":
        return BybitPublicAdapter()
    raise ValueError(f"Unsupported exchange: {exchange}")


def sync_market_inputs(
    *,
    engine,
    settings: AppSettings,
    now: datetime,
    instrument_cache: InstrumentCache | None = None,
) -> InputSyncResult:
    bars_written = 0
    derivatives_updated = 0
    instruments_selected = 0
    for exchange in settings.exchanges:
        adapter = _adapter_for_exchange(exchange)
        if instrument_cache is None:
            instruments = adapter.fetch_instruments()
        else:
            instruments = instrument_cache.get(adapter, now)
        market_result = sync_exchange_market_data(
            adapter=adapter,
            engine=engine,
            settings=settings,
            now=now,
            instruments=instruments,
        )
        derivative_result = sync_exchange_derivatives(
            adapter=adapter,
            engine=engine,
            settings=settings,
            now=now,
            instruments=instruments,
        )
        bars_written += market_result.bars_written
        derivatives_updated += derivative_result.updates_written
        instruments_selected += market_result.instruments_selected

    return InputSyncResult(
        status="healthy",
        message=(
            f"instruments_selected={instruments_selected} "
            f"bars_written={bars_written} derivatives_updated={derivatives_updated}"
        ),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = load_settings()
    engine = build_engine(settings)
    telegram_client = None
    if settings.telegram_bot_token and settings.telegram_chat_id:
        telegram_client = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
    logger.info("Starting daemon loop interval_seconds=%s", settings.signal_interval_seconds)
    instrument_cache = InstrumentCache(ttl_seconds=300)

    while True:
        now = _utc_now()
        try:
            sync_result = sync_market_inputs(
                engine=engine,
                settings=settings,
                now=now,
                instrument_cache=instrument_cache,
            )
            logger.info("Market input sync status=%s message=%s", sync_result.status, sync_result.message)
        except Exception:
            logger.exception("Market input sync failed; continuing with existing market data")

        try:
            result = run_once_pipeline(engine=engine, now=now)
            logger.info("Pipeline result status=%s message=%s", result.status, result.message)
        except Exception:
            logger.exception("Pipeline execution failed; skipping alert processing for this iteration")
            time.sleep(settings.signal_interval_seconds)
            continue

        try:
            inserted_alerts, sent_alerts = process_alerts(
                engine=engine,
                now=result.started_at,
                cooldown_seconds=settings.alert_cooldown_seconds,
                telegram_client=telegram_client,
            )
            logger.info("Alert processing completed inserted=%s sent=%s", inserted_alerts, sent_alerts)
        except Exception:
            logger.exception("Alert processing failed; continuing daemon loop")

        time.sleep(settings.signal_interval_seconds)


if __name__ == "__main__":
    main()
