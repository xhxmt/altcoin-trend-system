from __future__ import annotations

from datetime import datetime, timedelta


def compute_missing_1m_ranges(last_closed_ts: datetime | None, incoming_ts: datetime) -> list[tuple[datetime, datetime]]:
    if last_closed_ts is None:
        return []

    expected_next = last_closed_ts + timedelta(minutes=1)
    if incoming_ts <= expected_next:
        return []

    return [(expected_next, incoming_ts - timedelta(minutes=1))]
