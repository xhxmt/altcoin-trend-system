import altcoin_trend.db as db


class _FakeResource:
    def __init__(self, name: str, sql_text: str):
        self.name = name
        self._sql_text = sql_text

    def read_text(self, encoding: str = "utf-8") -> str:
        return self._sql_text


class _FakePackage:
    def __init__(self, resources):
        self._resources = resources

    def iterdir(self):
        return list(self._resources)


class _FakeConnection:
    def __init__(self, executed):
        self._executed = executed

    def execute(self, statement):
        self._executed.append(str(statement))


class _FakeBegin:
    def __init__(self, executed):
        self._executed = executed

    def __enter__(self):
        return _FakeConnection(self._executed)

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeEngine:
    def __init__(self):
        self.executed = []

    def begin(self):
        return _FakeBegin(self.executed)


def test_run_all_migrations_executes_packaged_sql_in_sorted_order(monkeypatch):
    fake_package = _FakePackage(
        [
            _FakeResource("003_signal_schema.sql", "signal"),
            _FakeResource("001_core_schema.sql", "core"),
            _FakeResource("002_raw_exchange.sql", "raw"),
        ]
    )
    monkeypatch.setattr(db.resources, "files", lambda package_name: fake_package)

    engine = _FakeEngine()

    db.run_all_migrations(engine)

    assert engine.executed == ["core", "raw", "signal"]
