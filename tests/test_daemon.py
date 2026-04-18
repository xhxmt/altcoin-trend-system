from types import SimpleNamespace

import pytest

from altcoin_trend.config import AppSettings
from altcoin_trend import daemon


class StopLoop(RuntimeError):
    pass


def test_daemon_runs_one_iteration_with_configured_sleep(monkeypatch):
    settings = AppSettings(signal_interval_seconds=17)
    pipeline_calls: list[int] = []
    sleep_calls: list[int] = []

    monkeypatch.setattr("altcoin_trend.daemon.load_settings", lambda: settings)
    monkeypatch.setattr(
        "altcoin_trend.daemon.run_once_pipeline",
        lambda: pipeline_calls.append(1) or SimpleNamespace(status="healthy", message="ok"),
    )

    def fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)
        raise StopLoop

    monkeypatch.setattr("altcoin_trend.daemon.time.sleep", fake_sleep)

    with pytest.raises(StopLoop):
        daemon.main()

    assert pipeline_calls == [1]
    assert sleep_calls == [17]
