from importlib import resources
from pathlib import Path

from sqlalchemy import Engine, create_engine, text

from altcoin_trend.config import AppSettings


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS_PACKAGE = "altcoin_trend.migrations"


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
