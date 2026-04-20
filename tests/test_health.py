from datetime import datetime, timezone

from altcoin_trend.health import (
    DatabaseHealth,
    ServiceHealth,
    format_health_report,
    load_database_health,
    load_service_health,
)


def test_format_health_report_includes_service_data_freshness_and_signal_counts():
    service = ServiceHealth(
        available=True,
        active_state="active",
        sub_state="running",
        main_pid="123",
        memory_current_bytes=123_456_789,
        error=None,
    )
    database = DatabaseHealth(
        latest_market_1m=datetime(2026, 4, 20, 2, 14, tzinfo=timezone.utc),
        market_lag_seconds=18.2,
        latest_feature=datetime(2026, 4, 20, 2, 13, tzinfo=timezone.utc),
        feature_lag_seconds=78.4,
        latest_rank=datetime(2026, 4, 20, 2, 13, tzinfo=timezone.utc),
        rank_lag_seconds=78.4,
        tier_counts={"monitor": 2, "rejected": 38},
        trade_candidates=0,
    )

    text = format_health_report(service=service, database=database)

    assert "Altcoin Trend Health" in text
    assert "Service: active/running pid=123 memory=117.7 MiB" in text
    assert "Market data: latest=2026-04-20T02:14:00+00:00 lag=18s" in text
    assert "Feature snapshot: latest=2026-04-20T02:13:00+00:00 lag=78s" in text
    assert "Rank snapshot: latest=2026-04-20T02:13:00+00:00 lag=78s" in text
    assert "Tiers: monitor=2 rejected=38" in text
    assert "Trade candidates: 0" in text


def test_load_service_health_parses_systemctl_show_output():
    class Result:
        returncode = 0
        stdout = "\n".join(
            [
                "ActiveState=active",
                "SubState=running",
                "MainPID=123",
                "MemoryCurrent=1048576",
            ]
        )
        stderr = ""

    def fake_run(args, capture_output, text, check):
        assert args[:3] == ["systemctl", "--user", "show"]
        assert capture_output is True
        assert text is True
        assert check is False
        return Result()

    service = load_service_health(run=fake_run)

    assert service.available is True
    assert service.active_state == "active"
    assert service.sub_state == "running"
    assert service.main_pid == "123"
    assert service.memory_current_bytes == 1_048_576


def test_load_service_health_reports_unavailable_systemctl():
    class Result:
        returncode = 1
        stdout = ""
        stderr = "not available"

    service = load_service_health(run=lambda *args, **kwargs: Result())

    assert service.available is False
    assert service.error == "not available"


def test_load_database_health_reads_freshness_tiers_and_candidates():
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def one(self):
            return self.rows[0]

        def all(self):
            return self.rows

    class Connection:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return Result(
                    [
                        {
                            "latest_market_1m": datetime(2026, 4, 20, 2, 14, tzinfo=timezone.utc),
                            "market_lag_seconds": 18.2,
                            "latest_feature": datetime(2026, 4, 20, 2, 13, tzinfo=timezone.utc),
                            "feature_lag_seconds": 78.4,
                            "latest_rank": datetime(2026, 4, 20, 2, 13, tzinfo=timezone.utc),
                            "rank_lag_seconds": 78.4,
                        }
                    ]
                )
            if self.calls == 2:
                return Result([{"tier": "monitor", "count": 2}, {"tier": "rejected", "count": 38}])
            return Result([{"count": 1}])

    class Begin:
        def __init__(self):
            self.connection = Connection()

        def __enter__(self):
            return self.connection

        def __exit__(self, exc_type, exc, tb):
            return False

    class Engine:
        def begin(self):
            return Begin()

    database = load_database_health(Engine())

    assert database.market_lag_seconds == 18.2
    assert database.feature_lag_seconds == 78.4
    assert database.rank_lag_seconds == 78.4
    assert database.tier_counts == {"monitor": 2, "rejected": 38}
    assert database.trade_candidates == 1
