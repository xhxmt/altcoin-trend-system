from pathlib import Path

from sqlalchemy import Engine, create_engine, text

from altcoin_trend.config import AppSettings


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_engine(settings: AppSettings) -> Engine:
    return create_engine(settings.database_url)


def run_sql_file(engine: Engine, relative_path: str) -> None:
    sql_path = _PROJECT_ROOT / relative_path
    sql_text = sql_path.read_text(encoding="utf-8")
    with engine.begin() as connection:
        connection.execute(text(sql_text))


def run_all_migrations(engine: Engine) -> None:
    for sql_file in sorted((_PROJECT_ROOT / "sql").glob("*.sql")):
        run_sql_file(engine, str(sql_file.relative_to(_PROJECT_ROOT)))
