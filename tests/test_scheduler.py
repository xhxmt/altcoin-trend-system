from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from altcoin_trend.scheduler import RunOnceResult, run_once_pipeline


def test_run_once_pipeline_defaults_to_degraded_when_no_step():
    result = run_once_pipeline()

    assert result.status == "degraded"
    assert result.message == "no pipeline step configured"
    assert result.started_at.tzinfo == timezone.utc


def test_run_once_pipeline_returns_healthy_step_message():
    result = run_once_pipeline(lambda: "pipeline complete")

    assert result.status == "healthy"
    assert result.message == "pipeline complete"


def test_run_once_result_is_frozen():
    result = RunOnceResult(datetime.now(timezone.utc), "healthy", "ok")

    with pytest.raises(FrozenInstanceError):
        result.status = "degraded"  # type: ignore[misc]
