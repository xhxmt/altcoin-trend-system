from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class RunOnceResult:
    started_at: datetime
    status: str
    message: str


def run_once_pipeline(step: Callable[[], str] | None = None) -> RunOnceResult:
    started_at = datetime.now(timezone.utc)
    if step is None:
        return RunOnceResult(started_at=started_at, status="degraded", message="no pipeline step configured")

    message = step()
    return RunOnceResult(started_at=started_at, status="healthy", message=message)
