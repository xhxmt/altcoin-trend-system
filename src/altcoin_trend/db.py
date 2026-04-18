from importlib import resources
from pathlib import Path
import re
from typing import Iterable

from sqlalchemy import Engine, create_engine, text

from altcoin_trend.config import AppSettings


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS_PACKAGE = "altcoin_trend.migrations"
_SAFE_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def build_engine(settings: AppSettings) -> Engine:
    return create_engine(settings.database_url)


def run_sql_file(engine: Engine, relative_path: str) -> None:
    sql_path = (_PROJECT_ROOT / relative_path).resolve()
    if not sql_path.is_relative_to(_PROJECT_ROOT):
        raise ValueError(f"Refusing to read SQL outside project root: {relative_path}")
    if not sql_path.is_file():
        raise FileNotFoundError(sql_path)
    sql_text = sql_path.read_text(encoding="utf-8")
    with engine.begin() as connection:
        connection.execute(text(sql_text))


def insert_rows(engine: Engine, table_name: str, rows: Iterable[dict]) -> int:
    rows = list(rows)
    if not rows:
        return 0

    table_parts = table_name.split(".")
    if len(table_parts) not in (1, 2) or any(_SAFE_IDENTIFIER_RE.fullmatch(part) is None for part in table_parts):
        raise ValueError(f"Invalid table name: {table_name}")

    first_row_keys = set(rows[0].keys())
    if not first_row_keys:
        raise ValueError("Rows must contain at least one column")
    for row in rows[1:]:
        if set(row.keys()) != first_row_keys:
            raise ValueError("All rows must have the same key set")

    columns = list(rows[0].keys())
    if any(not isinstance(column, str) or _SAFE_IDENTIFIER_RE.fullmatch(column) is None for column in columns):
        raise ValueError(f"Invalid column name in rows for table: {table_name}")
    column_sql = ", ".join(columns)
    placeholder_sql = ", ".join(f":{column}" for column in columns)
    statement = text(f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholder_sql})")

    with engine.begin() as connection:
        connection.execute(statement, rows)

    return len(rows)


def run_all_migrations(engine: Engine) -> None:
    migration_root = resources.files(_MIGRATIONS_PACKAGE)
    sql_files = sorted(
        (resource for resource in migration_root.iterdir() if resource.name.endswith(".sql")),
        key=lambda resource: resource.name,
    )
    for sql_file in sql_files:
        sql_text = sql_file.read_text(encoding="utf-8")
        with engine.begin() as connection:
            connection.execute(text(sql_text))
