from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from altcoin_trend.config import AppSettings
from altcoin_trend.ingest.bootstrap import filter_instruments


def _to_epoch_ms(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def _update_market_1m_derivative(connection, asset_id: int, ts: datetime, values: dict[str, float]) -> int:
    if not values:
        return 0
    assignments = ", ".join(f"{key} = :{key}" for key in values)
    statement = text(
        f"""
        UPDATE alt_core.market_1m
        SET {assignments}
        WHERE asset_id = :asset_id
          AND ts = :ts
        """
    )
    result = connection.execute(statement, {"asset_id": asset_id, "ts": ts, **values})
    rowcount = getattr(result, "rowcount", 0)
    return int(rowcount) if rowcount is not None and rowcount > 0 else 0


def _asset_ids_for_exchange(engine, exchange: str) -> dict[str, int]:
    statement = text(
        """
        SELECT symbol, asset_id
        FROM alt_core.asset_master
        WHERE exchange = :exchange
          AND market_type = 'usdt_perp'
        """
    )
    with engine.begin() as connection:
        return {
            row["symbol"]: int(row["asset_id"])
            for row in connection.execute(statement, {"exchange": exchange}).mappings()
        }


def bootstrap_derivatives(adapter, engine, settings: AppSettings, lookback_days: int, now: datetime) -> int:
    instruments = filter_instruments(adapter.fetch_instruments(), settings=settings, now=now)
    asset_ids = _asset_ids_for_exchange(engine, adapter.exchange)
    start_ms = _to_epoch_ms(now) - lookback_days * 86_400_000
    end_ms = _to_epoch_ms(now)
    updates = 0

    with engine.begin() as connection:
        for instrument in instruments:
            asset_id = asset_ids.get(instrument.symbol)
            if asset_id is None:
                continue
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
    return updates
