from __future__ import annotations

import logging
import time

from altcoin_trend.config import load_settings
from altcoin_trend.scheduler import run_once_pipeline


logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()
    logger.info("Starting daemon loop interval_seconds=%s", settings.signal_interval_seconds)

    while True:
        result = run_once_pipeline()
        logger.info("Pipeline result status=%s message=%s", result.status, result.message)
        time.sleep(settings.signal_interval_seconds)


if __name__ == "__main__":
    main()
