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


class _InsertFakeConnection:
    def __init__(self, statements):
        self.statements = statements

    def execute(self, statement, rows):
        self.statements.append((str(statement), list(rows)))


class _InsertFakeBegin:
    def __init__(self, statements):
        self.statements = statements

    def __enter__(self):
        return _InsertFakeConnection(self.statements)

    def __exit__(self, exc_type, exc, tb):
        return False


class _InsertFakeEngine:
    def __init__(self):
        self.statements = []

    def begin(self):
        return _InsertFakeBegin(self.statements)


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


def test_insert_rows_returns_zero_for_empty_input():
    engine = _InsertFakeEngine()

    assert db.insert_rows(engine, "bars", []) == 0
    assert engine.statements == []


def test_insert_rows_executes_insert_for_matching_rows():
    engine = _InsertFakeEngine()

    count = db.insert_rows(
        engine,
        "public.bars",
        [
            {"asset_id": 1, "symbol": "SOLUSDT"},
            {"asset_id": 2, "symbol": "ETHUSDT"},
        ],
    )

    assert count == 2
    assert engine.statements == [
        ("INSERT INTO public.bars (asset_id, symbol) VALUES (:asset_id, :symbol)", [
            {"asset_id": 1, "symbol": "SOLUSDT"},
            {"asset_id": 2, "symbol": "ETHUSDT"},
        ])
    ]


def test_insert_rows_rejects_mismatched_row_shapes():
    engine = _InsertFakeEngine()

    try:
        db.insert_rows(
            engine,
            "bars",
            [
                {"asset_id": 1, "symbol": "SOLUSDT"},
                {"asset_id": 2, "close": 101.0},
            ],
        )
    except ValueError as exc:
        assert "same key set" in str(exc)
    else:
        raise AssertionError("ValueError not raised")


def test_insert_rows_rejects_invalid_table_name():
    engine = _InsertFakeEngine()

    try:
        db.insert_rows(engine, "bars;drop", [{"asset_id": 1}])
    except ValueError as exc:
        assert "Invalid table name" in str(exc)
    else:
        raise AssertionError("ValueError not raised")
