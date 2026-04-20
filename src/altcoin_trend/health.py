from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from subprocess import CompletedProcess, run as subprocess_run
from typing import Callable

from sqlalchemy import Engine, text


@dataclass(frozen=True)
class ServiceHealth:
    available: bool
    active_state: str | None
    sub_state: str | None
    main_pid: str | None
    memory_current_bytes: int | None
    error: str | None = None


@dataclass(frozen=True)
class DatabaseHealth:
    latest_market_1m: datetime | None
    market_lag_seconds: float | None
    latest_feature: datetime | None
    feature_lag_seconds: float | None
    latest_rank: datetime | None
    rank_lag_seconds: float | None
    tier_counts: dict[str, int]
    trade_candidates: int


RunCommand = Callable[..., CompletedProcess[str]]


def _parse_systemctl_show(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def _parse_int(value: str | None) -> int | None:
    if value in (None, "", "[not set]"):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def load_service_health(
    unit: str = "altcoin-trend.service",
    run: RunCommand = subprocess_run,
) -> ServiceHealth:
    result = run(
        [
            "systemctl",
            "--user",
            "show",
            unit,
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "-p",
            "MainPID",
            "-p",
            "MemoryCurrent",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip() or f"systemctl exited {result.returncode}"
        return ServiceHealth(False, None, None, None, None, error)

    values = _parse_systemctl_show(result.stdout)
    return ServiceHealth(
        available=True,
        active_state=values.get("ActiveState"),
        sub_state=values.get("SubState"),
        main_pid=values.get("MainPID"),
        memory_current_bytes=_parse_int(values.get("MemoryCurrent")),
    )


def _seconds(value) -> float | None:
    if value is None:
        return None
    return float(value)


def load_database_health(engine: Engine) -> DatabaseHealth:
    freshness_sql = text(
        """
        WITH
        clock AS (SELECT NOW() AS now_ts),
        market AS (SELECT MAX(ts) AS latest_market_1m FROM alt_core.market_1m),
        features AS (SELECT MAX(ts) AS latest_feature FROM alt_signal.feature_snapshot),
        ranks AS (SELECT MAX(ts) AS latest_rank FROM alt_signal.rank_snapshot)
        SELECT
            market.latest_market_1m,
            EXTRACT(EPOCH FROM clock.now_ts - market.latest_market_1m) AS market_lag_seconds,
            features.latest_feature,
            EXTRACT(EPOCH FROM clock.now_ts - features.latest_feature) AS feature_lag_seconds,
            ranks.latest_rank,
            EXTRACT(EPOCH FROM clock.now_ts - ranks.latest_rank) AS rank_lag_seconds
        FROM clock, market, features, ranks
        """
    )
    tier_sql = text(
        """
        WITH latest AS (
            SELECT MAX(ts) AS ts
            FROM alt_signal.rank_snapshot
        )
        SELECT tier, COUNT(*) AS count
        FROM alt_signal.rank_snapshot AS r
        JOIN latest USING (ts)
        GROUP BY tier
        ORDER BY tier
        """
    )
    candidate_sql = text(
        """
        SELECT COUNT(*) AS count
        FROM alt_signal.feature_snapshot
        WHERE ts = (
            SELECT MAX(ts)
            FROM alt_signal.feature_snapshot
        )
          AND trade_candidate = TRUE
        """
    )

    with engine.begin() as connection:
        freshness = connection.execute(freshness_sql).mappings().one()
        tier_counts = {
            str(row["tier"]): int(row["count"])
            for row in connection.execute(tier_sql).mappings().all()
        }
        candidate_count = connection.execute(candidate_sql).mappings().one()

    return DatabaseHealth(
        latest_market_1m=freshness["latest_market_1m"],
        market_lag_seconds=_seconds(freshness["market_lag_seconds"]),
        latest_feature=freshness["latest_feature"],
        feature_lag_seconds=_seconds(freshness["feature_lag_seconds"]),
        latest_rank=freshness["latest_rank"],
        rank_lag_seconds=_seconds(freshness["rank_lag_seconds"]),
        tier_counts=tier_counts,
        trade_candidates=int(candidate_count["count"]),
    )


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "none"
    return value.isoformat()


def _format_lag(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    return f"{seconds:.0f}s"


def _format_memory(bytes_value: int | None) -> str:
    if bytes_value is None:
        return "unknown"
    mib = bytes_value / 1024 / 1024
    return f"{mib:.1f} MiB"


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return " ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def format_health_report(service: ServiceHealth, database: DatabaseHealth) -> str:
    if service.available:
        service_line = (
            f"Service: {service.active_state or 'unknown'}/{service.sub_state or 'unknown'} "
            f"pid={service.main_pid or 'unknown'} memory={_format_memory(service.memory_current_bytes)}"
        )
    else:
        service_line = f"Service: unavailable error={service.error or 'unknown'}"

    lines = [
        "Altcoin Trend Health",
        service_line,
        f"Market data: latest={_format_dt(database.latest_market_1m)} lag={_format_lag(database.market_lag_seconds)}",
        f"Feature snapshot: latest={_format_dt(database.latest_feature)} lag={_format_lag(database.feature_lag_seconds)}",
        f"Rank snapshot: latest={_format_dt(database.latest_rank)} lag={_format_lag(database.rank_lag_seconds)}",
        f"Tiers: {_format_counts(database.tier_counts)}",
        f"Trade candidates: {database.trade_candidates}",
    ]
    return "\n".join(lines)


def collect_health(engine: Engine) -> str:
    return format_health_report(
        service=load_service_health(),
        database=load_database_health(engine),
    )
