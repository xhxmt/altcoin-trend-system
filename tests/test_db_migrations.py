import altcoin_trend.db as db
from altcoin_trend.models import Instrument


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
        return _FakeResult([{"symbol": row["symbol"], "asset_id": index + 100} for index, row in enumerate(rows)])


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


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return list(self._rows)


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


def test_upsert_instruments_returns_asset_ids_and_records_statement():
    engine = _InsertFakeEngine()
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

    asset_ids = db.upsert_instruments(engine, [instrument])

    assert asset_ids == {"SOLUSDT": 100}
    statement, rows = engine.statements[0]
    assert "INSERT INTO alt_core.asset_master" in statement
    assert "ON CONFLICT (exchange, market_type, symbol)" in statement
    assert "RETURNING symbol, asset_id" in statement
    assert rows[0]["symbol"] == "SOLUSDT"


def test_upsert_instruments_returns_empty_mapping_for_empty_input():
    engine = _InsertFakeEngine()

    assert db.upsert_instruments(engine, []) == {}
    assert engine.statements == []
