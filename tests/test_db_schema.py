from pathlib import Path


def test_sql_files_create_required_schemas_and_tables():
    sql_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(Path("sql").glob("*.sql"))
    )

    assert "CREATE SCHEMA IF NOT EXISTS alt_ingest" in sql_text
    assert "CREATE SCHEMA IF NOT EXISTS alt_raw" in sql_text
    assert "CREATE SCHEMA IF NOT EXISTS alt_core" in sql_text
    assert "CREATE SCHEMA IF NOT EXISTS alt_signal" in sql_text
    assert "CREATE TABLE IF NOT EXISTS alt_core.asset_master" in sql_text
    assert "CREATE TABLE IF NOT EXISTS alt_core.market_1m" in sql_text
    assert "CREATE TABLE IF NOT EXISTS alt_core.market_bar" in sql_text
    assert "CREATE TABLE IF NOT EXISTS alt_signal.feature_snapshot" in sql_text
    assert "CREATE TABLE IF NOT EXISTS alt_signal.rank_snapshot" in sql_text
    assert "CREATE TABLE IF NOT EXISTS alt_signal.alert_events" in sql_text
