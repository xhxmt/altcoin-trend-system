from __future__ import annotations

import logging
import time

from altcoin_trend.config import load_settings
from altcoin_trend.db import build_engine
from altcoin_trend.scheduler import process_alerts, run_once_pipeline
from altcoin_trend.signals.telegram import TelegramClient


logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()
    engine = build_engine(settings)
    telegram_client = None
    if settings.telegram_bot_token and settings.telegram_chat_id:
        telegram_client = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
    logger.info("Starting daemon loop interval_seconds=%s", settings.signal_interval_seconds)

    while True:
        result = run_once_pipeline(engine=engine)
        logger.info("Pipeline result status=%s message=%s", result.status, result.message)
        inserted_alerts, sent_alerts = process_alerts(
            engine=engine,
            now=result.started_at,
            cooldown_seconds=settings.alert_cooldown_seconds,
            telegram_client=telegram_client,
        )
        logger.info("Alert processing completed inserted=%s sent=%s", inserted_alerts, sent_alerts)
        time.sleep(settings.signal_interval_seconds)


if __name__ == "__main__":
    main()
