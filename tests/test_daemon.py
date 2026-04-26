import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from altcoin_trend.config import AppSettings
from altcoin_trend import daemon
from altcoin_trend.models import Instrument


class StopLoop(RuntimeError):
    pass


def test_daemon_runs_one_iteration_with_configured_sleep(monkeypatch):
    settings = AppSettings(signal_interval_seconds=17, symbol_allowlist="SOLUSDT,ETHUSDT")
    calls: list[str] = []
    alert_calls: list[int] = []
    sleep_calls: list[int] = []
    engine = object()

    monkeypatch.setattr("altcoin_trend.daemon.load_settings", lambda: settings)
    monkeypatch.setattr("altcoin_trend.daemon.build_engine", lambda loaded_settings: engine)
    monkeypatch.setattr(
        "altcoin_trend.daemon.sync_market_inputs",
        lambda *, engine, settings, now, instrument_cache: calls.append("sync")
        or SimpleNamespace(status="healthy", message="bars_written=2 derivatives_updated=1"),
    )
    monkeypatch.setattr(
        "altcoin_trend.daemon.run_once_pipeline",
        lambda *, engine, snapshot_lookback_days, stale_market_seconds: calls.append("pipeline")
        or SimpleNamespace(status="healthy", message="ok", started_at=datetime.now(timezone.utc)),
    )
    monkeypatch.setattr(
        "altcoin_trend.daemon.process_alerts",
        lambda *, engine, now, cooldown_seconds, telegram_client: alert_calls.append(id(engine)) or (1, 0),
    )

    def fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)
        raise StopLoop

    monkeypatch.setattr("altcoin_trend.daemon.time.sleep", fake_sleep)

    with pytest.raises(StopLoop):
        daemon.main()

    assert calls == ["sync", "pipeline"]
    assert alert_calls == [id(engine)]
    assert sleep_calls == [17]


def test_daemon_runs_market_sync_without_allowlist_in_full_market_mode(monkeypatch):
    settings = AppSettings(signal_interval_seconds=17, symbol_allowlist="")
    calls: list[str] = []
    engine = object()

    monkeypatch.setattr("altcoin_trend.daemon.load_settings", lambda: settings)
    monkeypatch.setattr("altcoin_trend.daemon.build_engine", lambda loaded_settings: engine)
    monkeypatch.setattr(
        "altcoin_trend.daemon.sync_market_inputs",
        lambda *, engine, settings, now, instrument_cache: calls.append("sync")
        or SimpleNamespace(status="healthy", message="full-market"),
    )
    monkeypatch.setattr(
        "altcoin_trend.daemon.run_once_pipeline",
        lambda *, engine, snapshot_lookback_days, stale_market_seconds: calls.append("pipeline")
        or SimpleNamespace(status="healthy", message="ok", started_at=datetime.now(timezone.utc)),
    )
    monkeypatch.setattr(
        "altcoin_trend.daemon.process_alerts",
        lambda *, engine, now, cooldown_seconds, telegram_client: (0, 0),
    )

    def fake_sleep(seconds: int) -> None:
        raise StopLoop

    monkeypatch.setattr("altcoin_trend.daemon.time.sleep", fake_sleep)

    with pytest.raises(StopLoop):
        daemon.main()

    assert calls == ["sync", "pipeline"]


def test_daemon_reduces_httpx_request_log_noise(monkeypatch):
    settings = AppSettings(signal_interval_seconds=17, symbol_allowlist="")
    httpx_logger = logging.getLogger("httpx")
    original_level = httpx_logger.level
    httpx_logger.setLevel(logging.NOTSET)

    monkeypatch.setattr("altcoin_trend.daemon.load_settings", lambda: settings)
    monkeypatch.setattr("altcoin_trend.daemon.build_engine", lambda loaded_settings: object())
    monkeypatch.setattr(
        "altcoin_trend.daemon.run_once_pipeline",
        lambda *, engine, snapshot_lookback_days, stale_market_seconds: SimpleNamespace(
            status="healthy",
            message="ok",
            started_at=datetime.now(timezone.utc),
        ),
    )
    monkeypatch.setattr(
        "altcoin_trend.daemon.process_alerts",
        lambda *, engine, now, cooldown_seconds, telegram_client: (0, 0),
    )
    monkeypatch.setattr("altcoin_trend.daemon.time.sleep", lambda seconds: (_ for _ in ()).throw(StopLoop))

    try:
        with pytest.raises(StopLoop):
            daemon.main()
        assert httpx_logger.level == logging.WARNING
    finally:
        httpx_logger.setLevel(original_level)


def test_daemon_recovers_after_pipeline_failure_with_backoff(monkeypatch):
    settings = AppSettings(signal_interval_seconds=17, daemon_failure_backoff_max_seconds=30)
    engine = object()
    sleep_calls: list[int] = []
    pipeline_calls = 0
    alert_calls = 0

    monkeypatch.setattr("altcoin_trend.daemon.load_settings", lambda: settings)
    monkeypatch.setattr("altcoin_trend.daemon.build_engine", lambda loaded_settings: engine)
    monkeypatch.setattr(
        "altcoin_trend.daemon.sync_market_inputs",
        lambda *, engine, settings, now, instrument_cache: SimpleNamespace(status="healthy", message="ok"),
    )

    def fake_pipeline(*, engine, snapshot_lookback_days, stale_market_seconds):
        nonlocal pipeline_calls
        pipeline_calls += 1
        if pipeline_calls == 1:
            raise ConnectionError("temporary database outage")
        return SimpleNamespace(status="healthy", message="ok", started_at=datetime(2026, 1, 1, tzinfo=timezone.utc))

    def fake_alerts(*, engine, now, cooldown_seconds, telegram_client):
        nonlocal alert_calls
        alert_calls += 1
        return (0, 0)

    def fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) == 2:
            raise StopLoop

    monkeypatch.setattr("altcoin_trend.daemon.run_once_pipeline", fake_pipeline)
    monkeypatch.setattr("altcoin_trend.daemon.process_alerts", fake_alerts)
    monkeypatch.setattr("altcoin_trend.daemon.time.sleep", fake_sleep)

    with pytest.raises(StopLoop):
        daemon.main()

    assert pipeline_calls == 2
    assert alert_calls == 1
    assert sleep_calls == [1, 17]


def test_daemon_alert_failure_does_not_kill_subsequent_cycles(monkeypatch):
    settings = AppSettings(signal_interval_seconds=17, daemon_failure_backoff_max_seconds=30)
    engine = object()
    sleep_calls: list[int] = []
    alert_calls = 0

    monkeypatch.setattr("altcoin_trend.daemon.load_settings", lambda: settings)
    monkeypatch.setattr("altcoin_trend.daemon.build_engine", lambda loaded_settings: engine)
    monkeypatch.setattr(
        "altcoin_trend.daemon.sync_market_inputs",
        lambda *, engine, settings, now, instrument_cache: SimpleNamespace(status="healthy", message="ok"),
    )
    monkeypatch.setattr(
        "altcoin_trend.daemon.run_once_pipeline",
        lambda *, engine, snapshot_lookback_days, stale_market_seconds: SimpleNamespace(
            status="healthy",
            message="ok",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    )

    def fake_alerts(*, engine, now, cooldown_seconds, telegram_client):
        nonlocal alert_calls
        alert_calls += 1
        if alert_calls == 1:
            raise TimeoutError("telegram timeout")
        return (1, 1)

    def fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) == 2:
            raise StopLoop

    monkeypatch.setattr("altcoin_trend.daemon.process_alerts", fake_alerts)
    monkeypatch.setattr("altcoin_trend.daemon.time.sleep", fake_sleep)

    with pytest.raises(StopLoop):
        daemon.main()

    assert alert_calls == 2
    assert sleep_calls == [1, 17]


def test_daemon_backoff_increases_and_caps_on_repeated_failure(monkeypatch):
    settings = AppSettings(signal_interval_seconds=17, daemon_failure_backoff_max_seconds=2)
    sleep_calls: list[int] = []

    monkeypatch.setattr("altcoin_trend.daemon.load_settings", lambda: settings)
    monkeypatch.setattr("altcoin_trend.daemon.build_engine", lambda loaded_settings: object())
    monkeypatch.setattr(
        "altcoin_trend.daemon.sync_market_inputs",
        lambda *, engine, settings, now, instrument_cache: SimpleNamespace(status="healthy", message="ok"),
    )
    monkeypatch.setattr(
        "altcoin_trend.daemon.run_once_pipeline",
        lambda *, engine, snapshot_lookback_days, stale_market_seconds: (_ for _ in ()).throw(
            ConnectionError("database unavailable")
        ),
    )
    monkeypatch.setattr(
        "altcoin_trend.daemon.process_alerts",
        lambda *, engine, now, cooldown_seconds, telegram_client: (_ for _ in ()).throw(
            AssertionError("alerts should be skipped when pipeline fails")
        ),
    )

    def fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) == 4:
            raise StopLoop

    monkeypatch.setattr("altcoin_trend.daemon.time.sleep", fake_sleep)

    with pytest.raises(StopLoop):
        daemon.main()

    assert sleep_calls == [1, 2, 2, 2]


def test_daemon_fails_fast_for_invalid_runtime_config(monkeypatch):
    settings = AppSettings(default_exchanges="kraken")
    monkeypatch.setattr("altcoin_trend.daemon.load_settings", lambda: settings)

    with pytest.raises(ValueError, match="Unsupported ACTS_DEFAULT_EXCHANGES"):
        daemon.main()


def test_sync_market_inputs_reuses_fetched_instruments(monkeypatch):
    settings = AppSettings(default_exchanges="binance", symbol_allowlist="SOLUSDT")
    instrument = Instrument(
        exchange="binance",
        market_type="usdt_perp",
        symbol="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        status="trading",
        onboard_at=None,
        contract_type="PERPETUAL",
        tick_size=0.01,
        step_size=0.1,
        min_notional=5.0,
    )
    fetch_calls = []
    seen_instruments = []

    class Adapter:
        exchange = "binance"

        def fetch_instruments(self):
            fetch_calls.append("fetch")
            return [instrument]

    monkeypatch.setattr("altcoin_trend.daemon._adapter_for_exchange", lambda exchange: Adapter())

    def fake_market_sync(*, adapter, engine, settings, now, instruments):
        seen_instruments.append(("market", instruments))
        return SimpleNamespace(bars_written=2, instruments_selected=1)

    def fake_derivative_sync(*, adapter, engine, settings, now, instruments):
        seen_instruments.append(("derivatives", instruments))
        return SimpleNamespace(updates_written=3, instruments_selected=1)

    monkeypatch.setattr("altcoin_trend.daemon.sync_exchange_market_data", fake_market_sync)
    monkeypatch.setattr("altcoin_trend.daemon.sync_exchange_derivatives", fake_derivative_sync)

    result = daemon.sync_market_inputs(
        engine=object(),
        settings=settings,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert fetch_calls == ["fetch"]
    assert seen_instruments == [("market", [instrument]), ("derivatives", [instrument])]
    assert result.message == "instruments_selected=1 bars_written=2 derivatives_updated=3"


def test_sync_market_inputs_reports_market_failures(monkeypatch):
    settings = AppSettings(default_exchanges="binance", symbol_allowlist="SOLUSDT")
    instrument = Instrument(
        exchange="binance",
        market_type="usdt_perp",
        symbol="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        status="trading",
        onboard_at=None,
        contract_type="PERPETUAL",
        tick_size=0.01,
        step_size=0.1,
        min_notional=5.0,
    )

    class Adapter:
        exchange = "binance"

        def fetch_instruments(self):
            return [instrument]

    monkeypatch.setattr("altcoin_trend.daemon._adapter_for_exchange", lambda exchange: Adapter())
    monkeypatch.setattr(
        "altcoin_trend.daemon.sync_exchange_market_data",
        lambda *, adapter, engine, settings, now, instruments: SimpleNamespace(
            bars_written=2,
            instruments_selected=1,
            failed_symbols=1,
        ),
    )
    monkeypatch.setattr(
        "altcoin_trend.daemon.sync_exchange_derivatives",
        lambda *, adapter, engine, settings, now, instruments: SimpleNamespace(
            updates_written=3,
            instruments_selected=1,
        ),
    )

    result = daemon.sync_market_inputs(engine=object(), settings=settings, now=datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert result.message == "instruments_selected=1 bars_written=2 derivatives_updated=3 market_failures=1"


def test_sync_market_inputs_reuses_cached_instruments_across_calls(monkeypatch):
    settings = AppSettings(default_exchanges="binance", symbol_allowlist="SOLUSDT")
    instrument = Instrument(
        exchange="binance",
        market_type="usdt_perp",
        symbol="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        status="trading",
        onboard_at=None,
        contract_type="PERPETUAL",
        tick_size=0.01,
        step_size=0.1,
        min_notional=5.0,
    )
    fetch_calls = []

    class Adapter:
        exchange = "binance"

        def fetch_instruments(self):
            fetch_calls.append("fetch")
            return [instrument]

    monkeypatch.setattr("altcoin_trend.daemon._adapter_for_exchange", lambda exchange: Adapter())
    monkeypatch.setattr(
        "altcoin_trend.daemon.sync_exchange_market_data",
        lambda *, adapter, engine, settings, now, instruments: SimpleNamespace(bars_written=1, instruments_selected=1),
    )
    monkeypatch.setattr(
        "altcoin_trend.daemon.sync_exchange_derivatives",
        lambda *, adapter, engine, settings, now, instruments: SimpleNamespace(updates_written=0, instruments_selected=1),
    )

    cache = daemon.InstrumentCache(ttl_seconds=300)
    first = datetime(2026, 1, 1, tzinfo=timezone.utc)
    daemon.sync_market_inputs(engine=object(), settings=settings, now=first, instrument_cache=cache)
    daemon.sync_market_inputs(engine=object(), settings=settings, now=first + timedelta(seconds=299), instrument_cache=cache)

    assert fetch_calls == ["fetch"]


def test_instrument_cache_refreshes_after_ttl():
    instrument = Instrument(
        exchange="binance",
        market_type="usdt_perp",
        symbol="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        status="trading",
        onboard_at=None,
        contract_type="PERPETUAL",
        tick_size=0.01,
        step_size=0.1,
        min_notional=5.0,
    )
    fetch_calls = []

    class Adapter:
        exchange = "binance"

        def fetch_instruments(self):
            fetch_calls.append("fetch")
            return [instrument]

    cache = daemon.InstrumentCache(ttl_seconds=300)
    first = datetime(2026, 1, 1, tzinfo=timezone.utc)

    assert cache.get(Adapter(), first) == [instrument]
    assert cache.get(Adapter(), first + timedelta(seconds=301)) == [instrument]
    assert fetch_calls == ["fetch", "fetch"]
