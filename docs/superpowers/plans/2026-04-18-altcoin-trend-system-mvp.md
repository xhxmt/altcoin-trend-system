# Altcoin Trend System MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent Binance + Bybit USDT perpetual trend ranking daemon with PostgreSQL storage, CLI inspection, and Telegram alerts.

**Architecture:** Create a clean-room Python package named `altcoin_trend` in `/home/tfisher/altcoin-trend-system`. The system uses exchange-specific adapters, normalized PostgreSQL tables, local timeframe resampling, rule-based scoring, a signal state machine, and a daemon loop that combines REST bootstrap, WebSocket ingestion, and gap repair.

**Tech Stack:** Python 3.12+, Typer, pydantic-settings, SQLAlchemy, psycopg, pandas, numpy, httpx, websockets, pytest, PostgreSQL/TimescaleDB-compatible SQL.

---

## File Structure

Create this project structure:

```text
/home/tfisher/altcoin-trend-system/
  .gitignore
  pyproject.toml
  README.md
  config/acts.env.example
  sql/001_core_schema.sql
  sql/002_raw_exchange.sql
  sql/003_signal_schema.sql
  systemd/user/altcoin-trend.service
  src/altcoin_trend/__init__.py
  src/altcoin_trend/cli.py
  src/altcoin_trend/config.py
  src/altcoin_trend/db.py
  src/altcoin_trend/daemon.py
  src/altcoin_trend/scheduler.py
  src/altcoin_trend/models.py
  src/altcoin_trend/exchanges/__init__.py
  src/altcoin_trend/exchanges/base.py
  src/altcoin_trend/exchanges/binance.py
  src/altcoin_trend/exchanges/bybit.py
  src/altcoin_trend/exchanges/rate_limit.py
  src/altcoin_trend/exchanges/ws.py
  src/altcoin_trend/ingest/__init__.py
  src/altcoin_trend/ingest/bootstrap.py
  src/altcoin_trend/ingest/live.py
  src/altcoin_trend/ingest/repair.py
  src/altcoin_trend/ingest/normalize.py
  src/altcoin_trend/features/__init__.py
  src/altcoin_trend/features/indicators.py
  src/altcoin_trend/features/resample.py
  src/altcoin_trend/features/trend.py
  src/altcoin_trend/features/volume.py
  src/altcoin_trend/features/relative_strength.py
  src/altcoin_trend/features/derivatives.py
  src/altcoin_trend/features/quality.py
  src/altcoin_trend/features/scoring.py
  src/altcoin_trend/signals/__init__.py
  src/altcoin_trend/signals/ranking.py
  src/altcoin_trend/signals/state.py
  src/altcoin_trend/signals/alerts.py
  src/altcoin_trend/signals/telegram.py
  src/altcoin_trend/signals/explain.py
  tests/fixtures/binance_exchange_info.json
  tests/fixtures/binance_kline_ws.json
  tests/fixtures/bybit_instruments_info.json
  tests/fixtures/bybit_kline_ws.json
  tests/test_config.py
  tests/test_db_schema.py
  tests/test_exchange_contracts.py
  tests/test_rate_limit.py
  tests/test_resample.py
  tests/test_indicators.py
  tests/test_scoring.py
  tests/test_state.py
  tests/test_alerts.py
  tests/test_bootstrap.py
  tests/test_repair.py
  tests/test_cli.py
  tests/test_service_files.py
```

Responsibility boundaries:

```text
config.py: environment-driven settings only.
db.py: engine creation and SQL file execution only.
models.py: dataclasses/enums shared across layers.
exchanges/: exchange-specific public API parsing, REST fetch, WebSocket message parsing, and rate limiting.
ingest/: bootstrap, normalization, live write path, and gap repair orchestration.
features/: deterministic dataframe transforms and scoring math.
signals/: ranking snapshots, signal transitions, alert dedupe, Telegram delivery, and explain output.
cli.py: command registration and command orchestration.
daemon.py: long-running process entrypoint.
scheduler.py: periodic loop helpers.
```

## Task 1: Project Skeleton, Config, and CLI Shell

**Files:**
- Create: `.gitignore`
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `config/acts.env.example`
- Create: `src/altcoin_trend/__init__.py`
- Create: `src/altcoin_trend/config.py`
- Create: `src/altcoin_trend/cli.py`
- Test: `tests/test_config.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing config tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

from altcoin_trend.config import AppSettings, load_settings


def test_settings_defaults_point_to_project_paths(monkeypatch):
    monkeypatch.delenv("ACTS_OUTPUT_ROOT", raising=False)

    settings = AppSettings()

    assert settings.default_exchanges == "binance,bybit"
    assert settings.quote_asset == "USDT"
    assert settings.min_quote_volume_24h == 5_000_000
    assert settings.signal_interval_seconds == 60
    assert settings.output_root.endswith("artifacts")
    assert settings.artifacts_dir == Path(settings.output_root)


def test_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("ACTS_DATABASE_URL", "postgresql+psycopg://tester@/acts_test")
    monkeypatch.setenv("ACTS_SYMBOL_ALLOWLIST", "SOLUSDT,ARBUSDT")

    settings = load_settings()

    assert settings.database_url == "postgresql+psycopg://tester@/acts_test"
    assert settings.symbol_allowlist == "SOLUSDT,ARBUSDT"
    assert settings.allowlist_symbols == {"SOLUSDT", "ARBUSDT"}
```

- [ ] **Step 2: Write failing CLI tests**

Create `tests/test_cli.py`:

```python
from typer.testing import CliRunner

from altcoin_trend.cli import app


def test_cli_help_lists_mvp_commands():
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "init-db" in result.output
    assert "bootstrap" in result.output
    assert "run-once" in result.output
    assert "daemon" in result.output
    assert "rank" in result.output
    assert "status" in result.output
    assert "alerts" in result.output
    assert "explain" in result.output
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
pytest tests/test_config.py tests/test_cli.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'altcoin_trend'`.

- [ ] **Step 4: Create packaging and config implementation**

Create `.gitignore`:

```text
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
artifacts/
.env
```

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "altcoin-trend-system"
version = "0.1.0"
description = "Minute-to-hour altcoin USDT perpetual trend ranking daemon"
requires-python = ">=3.12"
dependencies = [
  "typer>=0.12.3",
  "pydantic-settings>=2.2.1",
  "sqlalchemy>=2.0.29",
  "psycopg[binary]>=3.1.19",
  "pandas>=2.2.2",
  "numpy>=1.26.4",
  "httpx>=0.27.0",
  "websockets>=12.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.1.1"]

[project.scripts]
acts = "altcoin_trend.cli:app"

[tool.pytest.ini_options]
pythonpath = ["src"]
```

Create `config/acts.env.example`:

```text
ACTS_DATABASE_URL=postgresql+psycopg://tfisher@/altcoin_trend
ACTS_OUTPUT_ROOT=/home/tfisher/altcoin-trend-system/artifacts
ACTS_DEFAULT_EXCHANGES=binance,bybit
ACTS_QUOTE_ASSET=USDT
ACTS_MIN_QUOTE_VOLUME_24H=5000000
ACTS_MIN_LISTING_DAYS=60
ACTS_BOOTSTRAP_LOOKBACK_DAYS=90
ACTS_SIGNAL_INTERVAL_SECONDS=60
ACTS_ALERT_COOLDOWN_SECONDS=14400
ACTS_TELEGRAM_BOT_TOKEN=
ACTS_TELEGRAM_CHAT_ID=
ACTS_SYMBOL_ALLOWLIST=
ACTS_SYMBOL_BLOCKLIST=
```

Create `src/altcoin_trend/__init__.py`:

```python
__all__ = ["__version__"]

__version__ = "0.1.0"
```

Create `src/altcoin_trend/config.py`:

```python
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class AppSettings(BaseSettings):
    database_url: str = "postgresql+psycopg://tfisher@/altcoin_trend"
    output_root: str = str(_PROJECT_ROOT / "artifacts")
    default_exchanges: str = "binance,bybit"
    quote_asset: str = "USDT"
    min_quote_volume_24h: float = 5_000_000
    min_listing_days: int = 60
    bootstrap_lookback_days: int = 90
    signal_interval_seconds: int = 60
    alert_cooldown_seconds: int = 14_400
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    symbol_allowlist: str = ""
    symbol_blocklist: str = ""

    model_config = SettingsConfigDict(env_prefix="ACTS_", extra="ignore")

    @property
    def artifacts_dir(self) -> Path:
        return Path(self.output_root)

    @property
    def exchanges(self) -> tuple[str, ...]:
        return tuple(item.strip() for item in self.default_exchanges.split(",") if item.strip())

    @property
    def allowlist_symbols(self) -> set[str]:
        return {item.strip().upper() for item in self.symbol_allowlist.split(",") if item.strip()}

    @property
    def blocklist_symbols(self) -> set[str]:
        return {item.strip().upper() for item in self.symbol_blocklist.split(",") if item.strip()}


def load_settings() -> AppSettings:
    return AppSettings()
```

- [ ] **Step 5: Create CLI shell**

Create `src/altcoin_trend/cli.py`:

```python
import typer

app = typer.Typer(help="Altcoin trend system CLI")


@app.callback()
def main() -> None:
    """Register a root callback so Typer keeps subcommand mode."""


@app.command("init-db")
def init_db() -> None:
    typer.echo("Database initialization is wired in Task 2")


@app.command("bootstrap")
def bootstrap(lookback_days: int = typer.Option(90, "--lookback-days", min=1)) -> None:
    typer.echo(f"Bootstrap requested for {lookback_days} days")


@app.command("run-once")
def run_once() -> None:
    typer.echo("Run-once scoring is wired in Task 9")


@app.command("daemon")
def daemon() -> None:
    typer.echo("Daemon loop is wired in Task 10")


@app.command("rank")
def rank(
    limit: int = typer.Option(30, "--limit", min=1),
    exchange: str | None = typer.Option(None, "--exchange"),
) -> None:
    scope = exchange or "all"
    typer.echo(f"Rank requested for scope={scope} limit={limit}")


@app.command("status")
def status() -> None:
    typer.echo("Status command is wired in Task 9")


@app.command("alerts")
def alerts(since: str = typer.Option("24h", "--since")) -> None:
    typer.echo(f"Alerts requested since {since}")


@app.command("explain")
def explain(symbol: str, exchange: str = typer.Option(..., "--exchange")) -> None:
    typer.echo(f"Explain requested for {exchange}:{symbol.upper()}")
```

Create `README.md`:

```markdown
# Altcoin Trend System

Minute-to-hour USDT perpetual trend scanner for Binance USD-M and Bybit linear markets.

The first version stores data in PostgreSQL/TimescaleDB-compatible tables, computes rule-based trend rankings, and sends Telegram alerts. It does not trade.

## Configuration

```bash
mkdir -p ~/.config/acts
cp config/acts.env.example ~/.config/acts/acts.env
```

## CLI

```bash
acts --help
acts init-db
acts bootstrap --lookback-days 90
acts run-once
acts rank --limit 30
acts explain SOLUSDT --exchange binance
```
```

- [ ] **Step 6: Run tests to verify they pass**

Run:

```bash
pytest tests/test_config.py tests/test_cli.py -q
```

Expected: PASS with `3 passed`.

- [ ] **Step 7: Commit**

Run:

```bash
git add .gitignore pyproject.toml README.md config/acts.env.example src/altcoin_trend/__init__.py src/altcoin_trend/config.py src/altcoin_trend/cli.py tests/test_config.py tests/test_cli.py
git commit -m "feat: add project skeleton and CLI shell"
```

## Task 2: Database Schema and `init-db`

**Files:**
- Create: `sql/001_core_schema.sql`
- Create: `sql/002_raw_exchange.sql`
- Create: `sql/003_signal_schema.sql`
- Create: `src/altcoin_trend/db.py`
- Modify: `src/altcoin_trend/cli.py`
- Test: `tests/test_db_schema.py`

- [ ] **Step 1: Write failing schema tests**

Create `tests/test_db_schema.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_db_schema.py -q
```

Expected: FAIL because the `sql` files do not exist.

- [ ] **Step 3: Create schema SQL**

Create `sql/001_core_schema.sql`:

```sql
CREATE SCHEMA IF NOT EXISTS alt_ingest;
CREATE SCHEMA IF NOT EXISTS alt_core;

CREATE TABLE IF NOT EXISTS alt_ingest.daemon_runs (
    run_id TEXT PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    error_message TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS alt_ingest.bootstrap_runs (
    run_id TEXT PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    exchange TEXT NOT NULL,
    lookback_days INTEGER NOT NULL,
    status TEXT NOT NULL,
    rows_written INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS alt_ingest.repair_runs (
    run_id TEXT PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    asset_id BIGINT,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    range_start TIMESTAMPTZ NOT NULL,
    range_end TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    rows_written INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS alt_core.asset_master (
    asset_id BIGSERIAL PRIMARY KEY,
    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    base_asset TEXT NOT NULL,
    quote_asset TEXT NOT NULL,
    status TEXT NOT NULL,
    onboard_at TIMESTAMPTZ,
    contract_type TEXT,
    tick_size DOUBLE PRECISION,
    step_size DOUBLE PRECISION,
    min_notional DOUBLE PRECISION,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (exchange, market_type, symbol)
);

CREATE TABLE IF NOT EXISTS alt_core.market_1m (
    ts TIMESTAMPTZ NOT NULL,
    asset_id BIGINT NOT NULL REFERENCES alt_core.asset_master(asset_id),
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    quote_volume DOUBLE PRECISION NOT NULL,
    trade_count BIGINT,
    taker_buy_base DOUBLE PRECISION,
    taker_buy_quote DOUBLE PRECISION,
    open_interest DOUBLE PRECISION,
    funding_rate DOUBLE PRECISION,
    long_short_ratio DOUBLE PRECISION,
    buy_sell_ratio DOUBLE PRECISION,
    data_status TEXT NOT NULL CHECK (data_status IN ('healthy', 'partial', 'stale')),
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (asset_id, ts)
);

CREATE TABLE IF NOT EXISTS alt_core.market_bar (
    ts TIMESTAMPTZ NOT NULL,
    asset_id BIGINT NOT NULL REFERENCES alt_core.asset_master(asset_id),
    timeframe TEXT NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    quote_volume DOUBLE PRECISION NOT NULL,
    trade_count BIGINT,
    taker_buy_base DOUBLE PRECISION,
    taker_buy_quote DOUBLE PRECISION,
    open_interest DOUBLE PRECISION,
    funding_rate DOUBLE PRECISION,
    long_short_ratio DOUBLE PRECISION,
    buy_sell_ratio DOUBLE PRECISION,
    data_status TEXT NOT NULL CHECK (data_status IN ('healthy', 'partial', 'stale')),
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (asset_id, timeframe, ts)
);
```

Create `sql/002_raw_exchange.sql`:

```sql
CREATE SCHEMA IF NOT EXISTS alt_raw;

CREATE TABLE IF NOT EXISTS alt_raw.exchange_messages (
    message_id BIGSERIAL PRIMARY KEY,
    exchange TEXT NOT NULL,
    stream_name TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS alt_raw.rest_fetch_runs (
    fetch_id BIGSERIAL PRIMARY KEY,
    exchange TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    symbol TEXT,
    range_start TIMESTAMPTZ,
    range_end TIMESTAMPTZ,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    row_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS alt_raw.ws_connection_events (
    event_id BIGSERIAL PRIMARY KEY,
    exchange TEXT NOT NULL,
    connection_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);
```

Create `sql/003_signal_schema.sql`:

```sql
CREATE SCHEMA IF NOT EXISTS alt_signal;

CREATE TABLE IF NOT EXISTS alt_signal.feature_snapshot (
    ts TIMESTAMPTZ NOT NULL,
    asset_id BIGINT NOT NULL REFERENCES alt_core.asset_master(asset_id),
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    ema20_1m DOUBLE PRECISION,
    ema20_4h DOUBLE PRECISION,
    ema60_4h DOUBLE PRECISION,
    ema20_1d DOUBLE PRECISION,
    ema60_1d DOUBLE PRECISION,
    adx14_4h DOUBLE PRECISION,
    atr14_4h DOUBLE PRECISION,
    volume_ratio_4h DOUBLE PRECISION,
    breakout_20d BOOLEAN NOT NULL DEFAULT FALSE,
    rs_btc_7d DOUBLE PRECISION,
    rs_eth_7d DOUBLE PRECISION,
    rs_btc_30d DOUBLE PRECISION,
    rs_eth_30d DOUBLE PRECISION,
    oi_delta_1h DOUBLE PRECISION,
    oi_delta_4h DOUBLE PRECISION,
    funding_zscore DOUBLE PRECISION,
    taker_buy_sell_ratio DOUBLE PRECISION,
    trend_score DOUBLE PRECISION NOT NULL,
    volume_breakout_score DOUBLE PRECISION NOT NULL,
    relative_strength_score DOUBLE PRECISION NOT NULL,
    derivatives_score DOUBLE PRECISION NOT NULL,
    quality_score DOUBLE PRECISION NOT NULL,
    final_score DOUBLE PRECISION NOT NULL,
    veto_reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (asset_id, ts)
);

CREATE TABLE IF NOT EXISTS alt_signal.rank_snapshot (
    ts TIMESTAMPTZ NOT NULL,
    rank_scope TEXT NOT NULL,
    rank INTEGER NOT NULL,
    asset_id BIGINT NOT NULL REFERENCES alt_core.asset_master(asset_id),
    symbol TEXT NOT NULL,
    base_asset TEXT NOT NULL,
    final_score DOUBLE PRECISION NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('strong', 'watchlist', 'monitor', 'rejected')),
    primary_reason TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (ts, rank_scope, rank)
);

CREATE TABLE IF NOT EXISTS alt_signal.alert_events (
    alert_id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    asset_id BIGINT NOT NULL REFERENCES alt_core.asset_master(asset_id),
    symbol TEXT NOT NULL,
    alert_type TEXT NOT NULL CHECK (alert_type IN ('strong_trend', 'watchlist_enter', 'breakout_confirmed', 'risk_downgrade')),
    final_score DOUBLE PRECISION NOT NULL,
    message TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    delivery_status TEXT NOT NULL CHECK (delivery_status IN ('pending', 'sent', 'failed', 'suppressed')),
    delivery_error TEXT
);
```

- [ ] **Step 4: Create DB helper**

Create `src/altcoin_trend/db.py`:

```python
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
```

Modify `src/altcoin_trend/cli.py` imports and `init_db`:

```python
from altcoin_trend.config import load_settings
from altcoin_trend.db import build_engine, run_all_migrations
```

```python
@app.command("init-db")
def init_db() -> None:
    settings = load_settings()
    engine = build_engine(settings)
    run_all_migrations(engine)
    typer.echo("Initialized altcoin trend database schema")
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
pytest tests/test_db_schema.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add sql src/altcoin_trend/db.py src/altcoin_trend/cli.py tests/test_db_schema.py
git commit -m "feat: add database schema and init command"
```

## Task 3: Shared Models, Rate Limiter, and Exchange Parser Contracts

**Files:**
- Create: `src/altcoin_trend/models.py`
- Create: `src/altcoin_trend/exchanges/__init__.py`
- Create: `src/altcoin_trend/exchanges/base.py`
- Create: `src/altcoin_trend/exchanges/rate_limit.py`
- Create: `src/altcoin_trend/exchanges/binance.py`
- Create: `src/altcoin_trend/exchanges/bybit.py`
- Create: `tests/fixtures/binance_exchange_info.json`
- Create: `tests/fixtures/binance_kline_ws.json`
- Create: `tests/fixtures/bybit_instruments_info.json`
- Create: `tests/fixtures/bybit_kline_ws.json`
- Test: `tests/test_rate_limit.py`
- Test: `tests/test_exchange_contracts.py`

- [ ] **Step 1: Write failing model and rate limiter tests**

Create `tests/test_rate_limit.py`:

```python
import time

from altcoin_trend.exchanges.rate_limit import TokenBucket


def test_token_bucket_allows_within_capacity():
    bucket = TokenBucket(capacity=10, refill_per_second=1)

    assert bucket.try_acquire(4) is True
    assert bucket.available == 6


def test_token_bucket_rejects_over_budget_without_sleeping():
    bucket = TokenBucket(capacity=3, refill_per_second=1)

    assert bucket.try_acquire(4) is False
    assert bucket.available == 3


def test_token_bucket_refills_over_time():
    bucket = TokenBucket(capacity=3, refill_per_second=10)
    assert bucket.try_acquire(3) is True

    time.sleep(0.12)

    assert bucket.try_acquire(1) is True
```

- [ ] **Step 2: Write failing exchange parser tests**

Create fixture `tests/fixtures/binance_exchange_info.json`:

```json
{
  "symbols": [
    {
      "symbol": "SOLUSDT",
      "pair": "SOLUSDT",
      "contractType": "PERPETUAL",
      "status": "TRADING",
      "baseAsset": "SOL",
      "quoteAsset": "USDT",
      "onboardDate": 1710000000000,
      "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.0100"},
        {"filterType": "LOT_SIZE", "stepSize": "0.1"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"}
      ]
    }
  ]
}
```

Create fixture `tests/fixtures/binance_kline_ws.json`:

```json
{
  "stream": "solusdt@kline_1m",
  "data": {
    "e": "kline",
    "E": 1710000060000,
    "s": "SOLUSDT",
    "k": {
      "t": 1710000000000,
      "T": 1710000059999,
      "s": "SOLUSDT",
      "i": "1m",
      "o": "100.0",
      "c": "101.0",
      "h": "102.0",
      "l": "99.5",
      "v": "1234.5",
      "n": 222,
      "x": true,
      "q": "124000.5",
      "V": "800.0",
      "Q": "80500.0"
    }
  }
}
```

Create fixture `tests/fixtures/bybit_instruments_info.json`:

```json
{
  "retCode": 0,
  "result": {
    "list": [
      {
        "symbol": "SOLUSDT",
        "status": "Trading",
        "baseCoin": "SOL",
        "quoteCoin": "USDT",
        "launchTime": "1710000000000",
        "contractType": "LinearPerpetual",
        "priceFilter": {"tickSize": "0.01"},
        "lotSizeFilter": {"qtyStep": "0.1", "minNotionalValue": "5"}
      }
    ]
  }
}
```

Create fixture `tests/fixtures/bybit_kline_ws.json`:

```json
{
  "topic": "kline.1.SOLUSDT",
  "type": "snapshot",
  "data": [
    {
      "start": 1710000000000,
      "end": 1710000059999,
      "interval": "1",
      "open": "100.0",
      "close": "101.0",
      "high": "102.0",
      "low": "99.5",
      "volume": "1234.5",
      "turnover": "124000.5",
      "confirm": true,
      "timestamp": 1710000060000
    }
  ]
}
```

Create `tests/test_exchange_contracts.py`:

```python
import json
from pathlib import Path

from altcoin_trend.exchanges.binance import BinancePublicAdapter
from altcoin_trend.exchanges.bybit import BybitPublicAdapter


FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_binance_exchange_info_parser_returns_usdt_perp_instrument():
    adapter = BinancePublicAdapter()

    instruments = adapter.parse_exchange_info(load_json("binance_exchange_info.json"))

    assert len(instruments) == 1
    instrument = instruments[0]
    assert instrument.exchange == "binance"
    assert instrument.symbol == "SOLUSDT"
    assert instrument.base_asset == "SOL"
    assert instrument.quote_asset == "USDT"
    assert instrument.tick_size == 0.01
    assert instrument.step_size == 0.1
    assert instrument.min_notional == 5.0


def test_binance_kline_ws_parser_returns_closed_bar():
    adapter = BinancePublicAdapter()

    bar = adapter.parse_kline_message(load_json("binance_kline_ws.json"))

    assert bar is not None
    assert bar.exchange == "binance"
    assert bar.symbol == "SOLUSDT"
    assert bar.close == 101.0
    assert bar.quote_volume == 124000.5
    assert bar.trade_count == 222
    assert bar.is_closed is True


def test_bybit_instruments_parser_returns_usdt_perp_instrument():
    adapter = BybitPublicAdapter()

    instruments = adapter.parse_instruments_info(load_json("bybit_instruments_info.json"))

    assert len(instruments) == 1
    instrument = instruments[0]
    assert instrument.exchange == "bybit"
    assert instrument.symbol == "SOLUSDT"
    assert instrument.contract_type == "LinearPerpetual"
    assert instrument.tick_size == 0.01
    assert instrument.step_size == 0.1


def test_bybit_kline_ws_parser_returns_closed_bar():
    adapter = BybitPublicAdapter()

    bar = adapter.parse_kline_message(load_json("bybit_kline_ws.json"), symbol="SOLUSDT")

    assert bar is not None
    assert bar.exchange == "bybit"
    assert bar.symbol == "SOLUSDT"
    assert bar.close == 101.0
    assert bar.quote_volume == 124000.5
    assert bar.is_closed is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
pytest tests/test_rate_limit.py tests/test_exchange_contracts.py -q
```

Expected: FAIL because exchange modules are missing.

- [ ] **Step 4: Create shared models**

Create `src/altcoin_trend/models.py`:

```python
from dataclasses import dataclass
from datetime import datetime, timezone


def utc_from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


@dataclass(frozen=True)
class Instrument:
    exchange: str
    market_type: str
    symbol: str
    base_asset: str
    quote_asset: str
    status: str
    onboard_at: datetime | None
    contract_type: str | None
    tick_size: float | None
    step_size: float | None
    min_notional: float | None


@dataclass(frozen=True)
class MarketBar1m:
    exchange: str
    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trade_count: int | None
    taker_buy_base: float | None
    taker_buy_quote: float | None
    is_closed: bool
```

- [ ] **Step 5: Create rate limiter**

Create `src/altcoin_trend/exchanges/__init__.py`:

```python
__all__ = []
```

Create `src/altcoin_trend/exchanges/rate_limit.py`:

```python
import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    capacity: float
    refill_per_second: float
    available: float = field(init=False)
    _updated_at: float = field(init=False)

    def __post_init__(self) -> None:
        self.available = self.capacity
        self._updated_at = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated_at
        self._updated_at = now
        self.available = min(self.capacity, self.available + elapsed * self.refill_per_second)

    def try_acquire(self, weight: float = 1) -> bool:
        self._refill()
        if weight > self.available:
            return False
        self.available -= weight
        return True
```

- [ ] **Step 6: Create exchange adapters**

Create `src/altcoin_trend/exchanges/base.py`:

```python
from typing import Protocol

from altcoin_trend.models import Instrument, MarketBar1m


class ExchangeAdapter(Protocol):
    exchange: str

    def parse_kline_message(self, payload: dict, symbol: str | None = None) -> MarketBar1m | None:
        ...

    def list_usdt_perp_symbols(self) -> list[str]:
        ...

    def fetch_klines_1m(self, symbol: str, start_ms: int, end_ms: int) -> list[MarketBar1m]:
        ...
```

Create `src/altcoin_trend/exchanges/binance.py`:

```python
from altcoin_trend.models import Instrument, MarketBar1m, utc_from_ms


def _filter_value(filters: list[dict], filter_type: str, key: str) -> float | None:
    for item in filters:
        if item.get("filterType") == filter_type and key in item:
            return float(item[key])
    return None


class BinancePublicAdapter:
    exchange = "binance"
    market_type = "usdt_perp"

    def parse_exchange_info(self, payload: dict) -> list[Instrument]:
        instruments: list[Instrument] = []
        for item in payload.get("symbols", []):
            if item.get("quoteAsset") != "USDT" or item.get("contractType") != "PERPETUAL":
                continue
            instruments.append(
                Instrument(
                    exchange=self.exchange,
                    market_type=self.market_type,
                    symbol=item["symbol"],
                    base_asset=item["baseAsset"],
                    quote_asset=item["quoteAsset"],
                    status=item["status"].lower(),
                    onboard_at=utc_from_ms(int(item["onboardDate"])) if item.get("onboardDate") else None,
                    contract_type=item.get("contractType"),
                    tick_size=_filter_value(item.get("filters", []), "PRICE_FILTER", "tickSize"),
                    step_size=_filter_value(item.get("filters", []), "LOT_SIZE", "stepSize"),
                    min_notional=_filter_value(item.get("filters", []), "MIN_NOTIONAL", "notional"),
                )
            )
        return instruments

    def parse_kline_message(self, payload: dict, symbol: str | None = None) -> MarketBar1m | None:
        data = payload.get("data", payload)
        kline = data.get("k", {})
        if not kline:
            return None
        return MarketBar1m(
            exchange=self.exchange,
            symbol=kline["s"],
            ts=utc_from_ms(int(kline["t"])),
            open=float(kline["o"]),
            high=float(kline["h"]),
            low=float(kline["l"]),
            close=float(kline["c"]),
            volume=float(kline["v"]),
            quote_volume=float(kline["q"]),
            trade_count=int(kline["n"]) if kline.get("n") is not None else None,
            taker_buy_base=float(kline["V"]) if kline.get("V") is not None else None,
            taker_buy_quote=float(kline["Q"]) if kline.get("Q") is not None else None,
            is_closed=bool(kline.get("x")),
        )
```

Create `src/altcoin_trend/exchanges/bybit.py`:

```python
from altcoin_trend.models import Instrument, MarketBar1m, utc_from_ms


class BybitPublicAdapter:
    exchange = "bybit"
    market_type = "usdt_perp"

    def parse_instruments_info(self, payload: dict) -> list[Instrument]:
        instruments: list[Instrument] = []
        for item in payload.get("result", {}).get("list", []):
            if item.get("quoteCoin") != "USDT" or item.get("contractType") != "LinearPerpetual":
                continue
            price_filter = item.get("priceFilter", {})
            lot_filter = item.get("lotSizeFilter", {})
            instruments.append(
                Instrument(
                    exchange=self.exchange,
                    market_type=self.market_type,
                    symbol=item["symbol"],
                    base_asset=item["baseCoin"],
                    quote_asset=item["quoteCoin"],
                    status=item["status"].lower(),
                    onboard_at=utc_from_ms(int(item["launchTime"])) if item.get("launchTime") else None,
                    contract_type=item.get("contractType"),
                    tick_size=float(price_filter["tickSize"]) if price_filter.get("tickSize") else None,
                    step_size=float(lot_filter["qtyStep"]) if lot_filter.get("qtyStep") else None,
                    min_notional=float(lot_filter["minNotionalValue"]) if lot_filter.get("minNotionalValue") else None,
                )
            )
        return instruments

    def parse_kline_message(self, payload: dict, symbol: str | None = None) -> MarketBar1m | None:
        rows = payload.get("data") or []
        if not rows:
            return None
        row = rows[0]
        topic_symbol = symbol or payload.get("topic", "").split(".")[-1]
        return MarketBar1m(
            exchange=self.exchange,
            symbol=topic_symbol,
            ts=utc_from_ms(int(row["start"])),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            quote_volume=float(row["turnover"]),
            trade_count=None,
            taker_buy_base=None,
            taker_buy_quote=None,
            is_closed=bool(row.get("confirm")),
        )
```

- [ ] **Step 7: Run tests to verify they pass**

Run:

```bash
pytest tests/test_rate_limit.py tests/test_exchange_contracts.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```bash
git add src/altcoin_trend/models.py src/altcoin_trend/exchanges tests/fixtures tests/test_rate_limit.py tests/test_exchange_contracts.py
git commit -m "feat: add exchange models and parsers"
```

## Task 4: Resampling and Indicator Math

**Files:**
- Create: `src/altcoin_trend/features/__init__.py`
- Create: `src/altcoin_trend/features/resample.py`
- Create: `src/altcoin_trend/features/indicators.py`
- Test: `tests/test_resample.py`
- Test: `tests/test_indicators.py`

- [ ] **Step 1: Write failing resample tests**

Create `tests/test_resample.py`:

```python
import pandas as pd

from altcoin_trend.features.resample import resample_market_1m


def test_resample_market_1m_builds_5m_ohlcv():
    frame = pd.DataFrame(
        [
            {"ts": "2026-01-01T00:00:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0, "quote_volume": 1000.0, "trade_count": 1},
            {"ts": "2026-01-01T00:01:00Z", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 20.0, "quote_volume": 2000.0, "trade_count": 2},
            {"ts": "2026-01-01T00:02:00Z", "open": 101.5, "high": 103.0, "low": 101.0, "close": 102.5, "volume": 30.0, "quote_volume": 3000.0, "trade_count": 3},
            {"ts": "2026-01-01T00:03:00Z", "open": 102.5, "high": 104.0, "low": 102.0, "close": 103.5, "volume": 40.0, "quote_volume": 4000.0, "trade_count": 4},
            {"ts": "2026-01-01T00:04:00Z", "open": 103.5, "high": 105.0, "low": 103.0, "close": 104.5, "volume": 50.0, "quote_volume": 5000.0, "trade_count": 5},
        ]
    )
    frame["ts"] = pd.to_datetime(frame["ts"])

    result = resample_market_1m(frame, "5m")

    assert len(result) == 1
    row = result.iloc[0]
    assert row["open"] == 100.0
    assert row["high"] == 105.0
    assert row["low"] == 99.0
    assert row["close"] == 104.5
    assert row["volume"] == 150.0
    assert row["quote_volume"] == 15000.0
    assert row["trade_count"] == 15
```

- [ ] **Step 2: Write failing indicator tests**

Create `tests/test_indicators.py`:

```python
import pandas as pd

from altcoin_trend.features.indicators import add_ema, true_range, atr, adx


def test_add_ema_adds_expected_column():
    frame = pd.DataFrame({"close": [100.0, 102.0, 104.0]})

    result = add_ema(frame, column="close", span=2, output="ema2")

    assert "ema2" in result.columns
    assert result["ema2"].iloc[-1] > result["ema2"].iloc[0]


def test_true_range_uses_previous_close():
    frame = pd.DataFrame(
        {
            "high": [10.0, 12.0],
            "low": [8.0, 9.0],
            "close": [9.0, 11.0],
        }
    )

    result = true_range(frame)

    assert list(result) == [2.0, 3.0]


def test_atr_returns_rolling_average_of_true_range():
    frame = pd.DataFrame(
        {
            "high": [10.0, 12.0, 13.0],
            "low": [8.0, 9.0, 10.0],
            "close": [9.0, 11.0, 12.0],
        }
    )

    result = atr(frame, window=2)

    assert round(float(result.iloc[-1]), 4) == 3.0


def test_adx_returns_series_with_same_length():
    frame = pd.DataFrame(
        {
            "high": [10.0, 12.0, 13.0, 15.0, 16.0],
            "low": [8.0, 9.0, 10.0, 12.0, 13.0],
            "close": [9.0, 11.0, 12.0, 14.0, 15.0],
        }
    )

    result = adx(frame, window=3)

    assert len(result) == len(frame)
    assert result.notna().any()
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
pytest tests/test_resample.py tests/test_indicators.py -q
```

Expected: FAIL because feature modules are missing.

- [ ] **Step 4: Create resampling implementation**

Create `src/altcoin_trend/features/__init__.py`:

```python
__all__ = []
```

Create `src/altcoin_trend/features/resample.py`:

```python
import pandas as pd


_PANDAS_FREQ = {
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}


def resample_market_1m(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe not in _PANDAS_FREQ:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    if frame.empty:
        return frame.copy()

    working = frame.copy()
    working["ts"] = pd.to_datetime(working["ts"], utc=True)
    working = working.set_index("ts").sort_index()

    aggregations = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "quote_volume": "sum",
        "trade_count": "sum",
    }
    for optional in ["taker_buy_base", "taker_buy_quote"]:
        if optional in working.columns:
            aggregations[optional] = "sum"
    for optional in ["open_interest", "funding_rate", "long_short_ratio", "buy_sell_ratio"]:
        if optional in working.columns:
            aggregations[optional] = "last"

    result = working.resample(_PANDAS_FREQ[timeframe], label="left", closed="left").agg(aggregations)
    result = result.dropna(subset=["open", "high", "low", "close"]).reset_index()
    return result
```

- [ ] **Step 5: Create indicator implementation**

Create `src/altcoin_trend/features/indicators.py`:

```python
import pandas as pd


def add_ema(frame: pd.DataFrame, column: str, span: int, output: str) -> pd.DataFrame:
    result = frame.copy()
    result[output] = result[column].ewm(span=span, adjust=False, min_periods=1).mean()
    return result


def true_range(frame: pd.DataFrame) -> pd.Series:
    previous_close = frame["close"].shift(1)
    high_low = frame["high"] - frame["low"]
    high_close = (frame["high"] - previous_close).abs()
    low_close = (frame["low"] - previous_close).abs()
    return pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)


def atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    return true_range(frame).rolling(window=window, min_periods=1).mean()


def adx(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]

    plus_dm = (high.diff()).clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    tr = true_range(pd.DataFrame({"high": high, "low": low, "close": close}))

    plus_di = 100 * plus_dm.rolling(window=window, min_periods=1).sum() / tr.rolling(window=window, min_periods=1).sum()
    minus_di = 100 * minus_dm.rolling(window=window, min_periods=1).sum() / tr.rolling(window=window, min_periods=1).sum()
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)) * 100
    return dx.rolling(window=window, min_periods=1).mean().fillna(0.0)
```

- [ ] **Step 6: Run tests to verify they pass**

Run:

```bash
pytest tests/test_resample.py tests/test_indicators.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/altcoin_trend/features tests/test_resample.py tests/test_indicators.py
git commit -m "feat: add resampling and indicator math"
```

## Task 5: Feature Scoring, Veto, Ranking, and Explain Output

**Files:**
- Create: `src/altcoin_trend/features/trend.py`
- Create: `src/altcoin_trend/features/volume.py`
- Create: `src/altcoin_trend/features/relative_strength.py`
- Create: `src/altcoin_trend/features/derivatives.py`
- Create: `src/altcoin_trend/features/quality.py`
- Create: `src/altcoin_trend/features/scoring.py`
- Create: `src/altcoin_trend/signals/__init__.py`
- Create: `src/altcoin_trend/signals/ranking.py`
- Create: `src/altcoin_trend/signals/explain.py`
- Modify: `src/altcoin_trend/cli.py`
- Test: `tests/test_scoring.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing scoring tests**

Create `tests/test_scoring.py`:

```python
from altcoin_trend.features.scoring import ScoreInput, compute_final_score, tier_for_score
from altcoin_trend.signals.ranking import rank_scores
from altcoin_trend.signals.explain import build_explain_text


def test_compute_final_score_uses_mvp_weights():
    score = compute_final_score(
        ScoreInput(
            trend_score=100.0,
            volume_breakout_score=80.0,
            relative_strength_score=60.0,
            derivatives_score=40.0,
            quality_score=100.0,
            veto_reason_codes=[],
        )
    )

    assert score.final_score == 78.0
    assert score.tier == "watchlist"


def test_veto_forces_rejected_tier():
    score = compute_final_score(
        ScoreInput(
            trend_score=100.0,
            volume_breakout_score=100.0,
            relative_strength_score=100.0,
            derivatives_score=100.0,
            quality_score=100.0,
            veto_reason_codes=["funding_extreme"],
        )
    )

    assert score.final_score == 100.0
    assert score.tier == "rejected"
    assert score.primary_reason == "funding_extreme"


def test_tier_for_score_boundaries():
    assert tier_for_score(85.0) == "strong"
    assert tier_for_score(75.0) == "watchlist"
    assert tier_for_score(60.0) == "monitor"
    assert tier_for_score(59.9) == "rejected"


def test_rank_scores_orders_by_final_score():
    rows = [
        {"asset_id": 1, "symbol": "AAAUSDT", "base_asset": "AAA", "final_score": 75.0, "tier": "watchlist"},
        {"asset_id": 2, "symbol": "BBBUSDT", "base_asset": "BBB", "final_score": 90.0, "tier": "strong"},
    ]

    ranked = rank_scores(rows, rank_scope="all")

    assert ranked[0]["rank"] == 1
    assert ranked[0]["symbol"] == "BBBUSDT"
    assert ranked[1]["rank"] == 2


def test_build_explain_text_contains_score_breakdown():
    text = build_explain_text(
        {
            "exchange": "binance",
            "symbol": "SOLUSDT",
            "final_score": 88.4,
            "tier": "strong",
            "trend_score": 31.0,
            "volume_breakout_score": 21.0,
            "relative_strength_score": 16.0,
            "derivatives_score": 12.0,
            "quality_score": 5.0,
            "veto_reason_codes": [],
        }
    )

    assert "SOLUSDT" in text
    assert "Score: 88.4" in text
    assert "Tier: strong" in text
    assert "Trend" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_scoring.py -q
```

Expected: FAIL because scoring modules are missing.

- [ ] **Step 3: Create scoring implementation**

Create `src/altcoin_trend/features/scoring.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreInput:
    trend_score: float
    volume_breakout_score: float
    relative_strength_score: float
    derivatives_score: float
    quality_score: float
    veto_reason_codes: list[str]


@dataclass(frozen=True)
class ScoreResult:
    final_score: float
    tier: str
    primary_reason: str


def tier_for_score(final_score: float) -> str:
    if final_score >= 85:
        return "strong"
    if final_score >= 75:
        return "watchlist"
    if final_score >= 60:
        return "monitor"
    return "rejected"


def compute_final_score(score_input: ScoreInput) -> ScoreResult:
    final_score = round(
        0.35 * score_input.trend_score
        + 0.25 * score_input.volume_breakout_score
        + 0.20 * score_input.relative_strength_score
        + 0.15 * score_input.derivatives_score
        + 0.05 * score_input.quality_score,
        4,
    )
    if score_input.veto_reason_codes:
        return ScoreResult(
            final_score=final_score,
            tier="rejected",
            primary_reason=score_input.veto_reason_codes[0],
        )
    return ScoreResult(final_score=final_score, tier=tier_for_score(final_score), primary_reason="")
```

Create each score group module with this content:

`src/altcoin_trend/features/trend.py`

```python
def clamp_score(value: float) -> float:
    return max(0.0, min(100.0, value))
```

`src/altcoin_trend/features/volume.py`

```python
def clamp_score(value: float) -> float:
    return max(0.0, min(100.0, value))
```

`src/altcoin_trend/features/relative_strength.py`

```python
def clamp_score(value: float) -> float:
    return max(0.0, min(100.0, value))
```

`src/altcoin_trend/features/derivatives.py`

```python
def clamp_score(value: float) -> float:
    return max(0.0, min(100.0, value))
```

`src/altcoin_trend/features/quality.py`

```python
def clamp_score(value: float) -> float:
    return max(0.0, min(100.0, value))
```

- [ ] **Step 4: Create ranking and explain implementation**

Create `src/altcoin_trend/signals/__init__.py`:

```python
__all__ = []
```

Create `src/altcoin_trend/signals/ranking.py`:

```python
from typing import Iterable


def rank_scores(rows: Iterable[dict], rank_scope: str) -> list[dict]:
    sorted_rows = sorted(rows, key=lambda row: row["final_score"], reverse=True)
    ranked: list[dict] = []
    for index, row in enumerate(sorted_rows, start=1):
        ranked_row = dict(row)
        ranked_row["rank_scope"] = rank_scope
        ranked_row["rank"] = index
        ranked.append(ranked_row)
    return ranked
```

Create `src/altcoin_trend/signals/explain.py`:

```python
def build_explain_text(row: dict) -> str:
    veto = row.get("veto_reason_codes") or []
    veto_text = ", ".join(veto) if veto else "none"
    return "\n".join(
        [
            f"{row.get('exchange', 'unknown')}:{row['symbol']}",
            f"Score: {row['final_score']}",
            f"Tier: {row['tier']}",
            "Breakdown:",
            f"- Trend: {row.get('trend_score', 0)}",
            f"- Volume: {row.get('volume_breakout_score', 0)}",
            f"- Relative Strength: {row.get('relative_strength_score', 0)}",
            f"- Derivatives: {row.get('derivatives_score', 0)}",
            f"- Quality: {row.get('quality_score', 0)}",
            f"Veto: {veto_text}",
        ]
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
pytest tests/test_scoring.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/altcoin_trend/features src/altcoin_trend/signals tests/test_scoring.py
git commit -m "feat: add scoring and explain helpers"
```

## Task 6: Signal State Machine, Alert Dedupe, and Telegram Delivery

**Files:**
- Create: `src/altcoin_trend/signals/state.py`
- Create: `src/altcoin_trend/signals/alerts.py`
- Create: `src/altcoin_trend/signals/telegram.py`
- Test: `tests/test_state.py`
- Test: `tests/test_alerts.py`

- [ ] **Step 1: Write failing state and alert tests**

Create `tests/test_state.py`:

```python
from altcoin_trend.signals.state import AlertDecision, evaluate_transition


def test_strong_transition_emits_strong_alert():
    decision = evaluate_transition(
        previous_tier="watchlist",
        current_tier="strong",
        breakout_confirmed=True,
        oi_confirmed=True,
        veto_reason_codes=[],
    )

    assert decision == AlertDecision(alert_type="strong_trend", should_alert=True)


def test_veto_after_watchlist_emits_risk_downgrade():
    decision = evaluate_transition(
        previous_tier="watchlist",
        current_tier="rejected",
        breakout_confirmed=False,
        oi_confirmed=False,
        veto_reason_codes=["funding_extreme"],
    )

    assert decision == AlertDecision(alert_type="risk_downgrade", should_alert=True)


def test_monitor_to_monitor_has_no_alert():
    decision = evaluate_transition(
        previous_tier="monitor",
        current_tier="monitor",
        breakout_confirmed=False,
        oi_confirmed=False,
        veto_reason_codes=[],
    )

    assert decision.should_alert is False
```

Create `tests/test_alerts.py`:

```python
from datetime import datetime, timedelta, timezone

from altcoin_trend.signals.alerts import AlertCooldown, build_strong_alert_message
from altcoin_trend.signals.telegram import TelegramClient


def test_alert_cooldown_suppresses_duplicate_alert():
    cooldown = AlertCooldown(cooldown_seconds=3600)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    assert cooldown.should_send("binance", "SOLUSDT", "strong_trend", now) is True
    cooldown.record_sent("binance", "SOLUSDT", "strong_trend", now)
    assert cooldown.should_send("binance", "SOLUSDT", "strong_trend", now + timedelta(minutes=10)) is False
    assert cooldown.should_send("binance", "SOLUSDT", "strong_trend", now + timedelta(hours=2)) is True


def test_build_strong_alert_message_contains_reasons():
    message = build_strong_alert_message(
        {
            "exchange": "binance",
            "symbol": "SOLUSDT",
            "final_score": 88.4,
            "trend_score": 31,
            "volume_breakout_score": 21,
            "relative_strength_score": 16,
            "derivatives_score": 12,
            "quality_score": 5,
            "reasons": ["20d breakout", "OI +8.3% 4h"],
            "risks": ["funding warm"],
        }
    )

    assert "[STRONG] SOLUSDT Binance" in message
    assert "20d breakout" in message
    assert "funding warm" in message


def test_telegram_client_requires_token_and_chat_id():
    client = TelegramClient(bot_token="", chat_id="")

    result = client.send_message("hello")

    assert result.ok is False
    assert "missing" in result.error
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_state.py tests/test_alerts.py -q
```

Expected: FAIL because signal modules are missing.

- [ ] **Step 3: Create state machine implementation**

Create `src/altcoin_trend/signals/state.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class AlertDecision:
    alert_type: str
    should_alert: bool


_TIER_ORDER = {"rejected": 0, "monitor": 1, "watchlist": 2, "strong": 3}


def evaluate_transition(
    previous_tier: str,
    current_tier: str,
    breakout_confirmed: bool,
    oi_confirmed: bool,
    veto_reason_codes: list[str],
) -> AlertDecision:
    if previous_tier in {"strong", "watchlist"} and (veto_reason_codes or _TIER_ORDER[current_tier] < _TIER_ORDER[previous_tier]):
        return AlertDecision(alert_type="risk_downgrade", should_alert=True)
    if previous_tier != "strong" and current_tier == "strong" and breakout_confirmed and oi_confirmed and not veto_reason_codes:
        return AlertDecision(alert_type="strong_trend", should_alert=True)
    if _TIER_ORDER[previous_tier] < _TIER_ORDER["watchlist"] and current_tier == "watchlist" and not veto_reason_codes:
        return AlertDecision(alert_type="watchlist_enter", should_alert=True)
    if breakout_confirmed and not veto_reason_codes:
        return AlertDecision(alert_type="breakout_confirmed", should_alert=True)
    return AlertDecision(alert_type="", should_alert=False)
```

- [ ] **Step 4: Create alert and Telegram implementation**

Create `src/altcoin_trend/signals/alerts.py`:

```python
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class AlertCooldown:
    cooldown_seconds: int
    _last_sent: dict[tuple[str, str, str], datetime] = field(default_factory=dict)

    def should_send(self, exchange: str, symbol: str, alert_type: str, now: datetime) -> bool:
        key = (exchange, symbol, alert_type)
        last_sent = self._last_sent.get(key)
        if last_sent is None:
            return True
        return now - last_sent >= timedelta(seconds=self.cooldown_seconds)

    def record_sent(self, exchange: str, symbol: str, alert_type: str, now: datetime) -> None:
        self._last_sent[(exchange, symbol, alert_type)] = now


def build_strong_alert_message(row: dict) -> str:
    exchange_name = str(row["exchange"]).title()
    reasons = ", ".join(row.get("reasons") or [])
    risks = ", ".join(row.get("risks") or ["none"])
    return "\n".join(
        [
            f"[STRONG] {row['symbol']} {exchange_name}",
            f"Score: {row['final_score']}",
            f"Trend {row.get('trend_score', 0)} | Volume {row.get('volume_breakout_score', 0)} | RS {row.get('relative_strength_score', 0)} | Deriv {row.get('derivatives_score', 0)} | Quality {row.get('quality_score', 0)}",
            f"Reasons: {reasons}",
            f"Risk: {risks}",
        ]
    )
```

Create `src/altcoin_trend/signals/telegram.py`:

```python
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class TelegramResult:
    ok: bool
    error: str = ""


@dataclass
class TelegramClient:
    bot_token: str
    chat_id: str
    timeout_seconds: float = 10.0

    def send_message(self, text: str) -> TelegramResult:
        if not self.bot_token or not self.chat_id:
            return TelegramResult(ok=False, error="missing Telegram bot token or chat id")
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            response = httpx.post(
                url,
                json={"chat_id": self.chat_id, "text": text},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return TelegramResult(ok=False, error=str(exc))
        return TelegramResult(ok=True)
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
pytest tests/test_state.py tests/test_alerts.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/altcoin_trend/signals/state.py src/altcoin_trend/signals/alerts.py src/altcoin_trend/signals/telegram.py tests/test_state.py tests/test_alerts.py
git commit -m "feat: add signal state and alerts"
```

## Task 7: REST Bootstrap and Normalization

**Files:**
- Create: `src/altcoin_trend/ingest/__init__.py`
- Create: `src/altcoin_trend/ingest/normalize.py`
- Create: `src/altcoin_trend/ingest/bootstrap.py`
- Modify: `src/altcoin_trend/exchanges/binance.py`
- Modify: `src/altcoin_trend/exchanges/bybit.py`
- Modify: `src/altcoin_trend/cli.py`
- Test: `tests/test_bootstrap.py`

- [ ] **Step 1: Write failing bootstrap tests**

Create `tests/test_bootstrap.py`:

```python
from datetime import datetime, timezone

from altcoin_trend.config import AppSettings
from altcoin_trend.ingest.bootstrap import filter_instruments
from altcoin_trend.models import Instrument


def instrument(symbol: str, quote_asset: str = "USDT", status: str = "trading", onboard_year: int = 2020) -> Instrument:
    return Instrument(
        exchange="binance",
        market_type="usdt_perp",
        symbol=symbol,
        base_asset=symbol.replace("USDT", ""),
        quote_asset=quote_asset,
        status=status,
        onboard_at=datetime(onboard_year, 1, 1, tzinfo=timezone.utc),
        contract_type="PERPETUAL",
        tick_size=0.01,
        step_size=0.1,
        min_notional=5.0,
    )


def test_filter_instruments_keeps_trading_usdt_perps(monkeypatch):
    settings = AppSettings(symbol_blocklist="BADUSDT")

    selected = filter_instruments(
        [
            instrument("SOLUSDT"),
            instrument("ETHBUSD", quote_asset="BUSD"),
            instrument("HALTUSDT", status="settling"),
            instrument("BADUSDT"),
        ],
        settings=settings,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert [item.symbol for item in selected] == ["SOLUSDT"]


def test_filter_instruments_honors_allowlist():
    settings = AppSettings(symbol_allowlist="ARBUSDT")

    selected = filter_instruments(
        [instrument("SOLUSDT"), instrument("ARBUSDT")],
        settings=settings,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert [item.symbol for item in selected] == ["ARBUSDT"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_bootstrap.py -q
```

Expected: FAIL because `altcoin_trend.ingest.bootstrap` is missing.

- [ ] **Step 3: Create bootstrap filtering implementation**

Create `src/altcoin_trend/ingest/__init__.py`:

```python
__all__ = []
```

Create `src/altcoin_trend/ingest/bootstrap.py`:

```python
from datetime import datetime

from altcoin_trend.config import AppSettings
from altcoin_trend.models import Instrument


def filter_instruments(
    instruments: list[Instrument],
    settings: AppSettings,
    now: datetime,
) -> list[Instrument]:
    selected: list[Instrument] = []
    allowlist = settings.allowlist_symbols
    blocklist = settings.blocklist_symbols
    for instrument in instruments:
        if instrument.quote_asset != settings.quote_asset:
            continue
        if instrument.market_type != "usdt_perp":
            continue
        if instrument.status != "trading":
            continue
        if instrument.symbol in blocklist:
            continue
        if allowlist and instrument.symbol not in allowlist:
            continue
        if instrument.onboard_at is not None:
            listing_age_days = (now - instrument.onboard_at).days
            if listing_age_days < settings.min_listing_days:
                continue
        selected.append(instrument)
    return selected
```

Create `src/altcoin_trend/ingest/normalize.py`:

```python
from altcoin_trend.models import MarketBar1m


def market_bar_to_row(asset_id: int, bar: MarketBar1m, data_status: str = "healthy") -> dict:
    return {
        "asset_id": asset_id,
        "exchange": bar.exchange,
        "symbol": bar.symbol,
        "ts": bar.ts,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "quote_volume": bar.quote_volume,
        "trade_count": bar.trade_count,
        "taker_buy_base": bar.taker_buy_base,
        "taker_buy_quote": bar.taker_buy_quote,
        "data_status": data_status,
        "reason_codes": [],
    }
```

- [ ] **Step 4: Add REST method signatures to adapters**

Append to `src/altcoin_trend/exchanges/binance.py` inside `BinancePublicAdapter`:

```python
    def list_usdt_perp_symbols(self) -> list[str]:
        raise NotImplementedError("HTTP fetching is added after parser contract tests")

    def fetch_klines_1m(self, symbol: str, start_ms: int, end_ms: int) -> list[MarketBar1m]:
        raise NotImplementedError("HTTP fetching is added after parser contract tests")
```

Append to `src/altcoin_trend/exchanges/bybit.py` inside `BybitPublicAdapter`:

```python
    def list_usdt_perp_symbols(self) -> list[str]:
        raise NotImplementedError("HTTP fetching is added after parser contract tests")

    def fetch_klines_1m(self, symbol: str, start_ms: int, end_ms: int) -> list[MarketBar1m]:
        raise NotImplementedError("HTTP fetching is added after parser contract tests")
```

- [ ] **Step 5: Connect CLI bootstrap command to selected settings**

Modify `src/altcoin_trend/cli.py` `bootstrap` command:

```python
@app.command("bootstrap")
def bootstrap(lookback_days: int = typer.Option(90, "--lookback-days", min=1)) -> None:
    settings = load_settings()
    typer.echo(
        "Bootstrap requested "
        f"lookback_days={lookback_days} exchanges={','.join(settings.exchanges)} quote={settings.quote_asset}"
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run:

```bash
pytest tests/test_bootstrap.py tests/test_exchange_contracts.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/altcoin_trend/ingest src/altcoin_trend/exchanges src/altcoin_trend/cli.py tests/test_bootstrap.py
git commit -m "feat: add bootstrap filtering and normalization"
```

## Task 8: Gap Repair and WebSocket Message Handling

**Files:**
- Create: `src/altcoin_trend/ingest/repair.py`
- Create: `src/altcoin_trend/ingest/live.py`
- Create: `src/altcoin_trend/exchanges/ws.py`
- Test: `tests/test_repair.py`

- [ ] **Step 1: Write failing repair tests**

Create `tests/test_repair.py`:

```python
from datetime import datetime, timezone

from altcoin_trend.ingest.repair import compute_missing_1m_ranges


def dt(minute: int) -> datetime:
    return datetime(2026, 1, 1, 0, minute, tzinfo=timezone.utc)


def test_compute_missing_1m_ranges_returns_empty_for_next_bar():
    assert compute_missing_1m_ranges(last_closed_ts=dt(0), incoming_ts=dt(1)) == []


def test_compute_missing_1m_ranges_returns_gap_range():
    ranges = compute_missing_1m_ranges(last_closed_ts=dt(0), incoming_ts=dt(4))

    assert ranges == [(dt(1), dt(3))]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_repair.py -q
```

Expected: FAIL because repair module is missing.

- [ ] **Step 3: Create gap repair helper**

Create `src/altcoin_trend/ingest/repair.py`:

```python
from datetime import datetime, timedelta


def compute_missing_1m_ranges(
    last_closed_ts: datetime | None,
    incoming_ts: datetime,
) -> list[tuple[datetime, datetime]]:
    if last_closed_ts is None:
        return []
    expected_next = last_closed_ts + timedelta(minutes=1)
    if incoming_ts <= expected_next:
        return []
    return [(expected_next, incoming_ts - timedelta(minutes=1))]
```

Create `src/altcoin_trend/ingest/live.py`:

```python
from altcoin_trend.models import MarketBar1m


def accept_closed_bar(bar: MarketBar1m) -> bool:
    return bar.is_closed
```

Create `src/altcoin_trend/exchanges/ws.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class StreamSubscription:
    exchange: str
    stream_name: str
    symbol: str | None = None


def binance_kline_stream_name(symbol: str) -> str:
    return f"{symbol.lower()}@kline_1m"


def bybit_kline_topic(symbol: str) -> str:
    return f"kline.1.{symbol.upper()}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_repair.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/altcoin_trend/ingest/repair.py src/altcoin_trend/ingest/live.py src/altcoin_trend/exchanges/ws.py tests/test_repair.py
git commit -m "feat: add live ingestion repair helpers"
```

## Task 9: Run-Once Pipeline, CLI Rank/Explain, and Status

**Files:**
- Create: `src/altcoin_trend/scheduler.py`
- Modify: `src/altcoin_trend/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Extend CLI tests for stable command output**

Append to `tests/test_cli.py`:

```python
def test_explain_command_echoes_requested_symbol():
    result = CliRunner().invoke(app, ["explain", "solusdt", "--exchange", "binance"])

    assert result.exit_code == 0
    assert "binance:SOLUSDT" in result.output


def test_rank_command_accepts_exchange_filter():
    result = CliRunner().invoke(app, ["rank", "--exchange", "bybit", "--limit", "5"])

    assert result.exit_code == 0
    assert "scope=bybit" in result.output
    assert "limit=5" in result.output
```

- [ ] **Step 2: Run tests to verify current behavior**

Run:

```bash
pytest tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 3: Create scheduler helper**

Create `src/altcoin_trend/scheduler.py`:

```python
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class RunOnceResult:
    started_at: datetime
    status: str
    message: str


def run_once_pipeline(step: Callable[[], str] | None = None) -> RunOnceResult:
    started_at = datetime.now(timezone.utc)
    if step is None:
        return RunOnceResult(started_at=started_at, status="degraded", message="no pipeline step configured")
    message = step()
    return RunOnceResult(started_at=started_at, status="healthy", message=message)
```

- [ ] **Step 4: Wire run-once and status commands**

Modify `src/altcoin_trend/cli.py` imports:

```python
from altcoin_trend.scheduler import run_once_pipeline
```

Modify `run_once`:

```python
@app.command("run-once")
def run_once() -> None:
    result = run_once_pipeline()
    typer.echo(f"Run once status={result.status} message={result.message}")
```

Modify `status`:

```python
@app.command("status")
def status() -> None:
    settings = load_settings()
    typer.echo(
        "Status: configured "
        f"exchanges={','.join(settings.exchanges)} interval={settings.signal_interval_seconds}s"
    )
```

Modify `explain`:

```python
@app.command("explain")
def explain(symbol: str, exchange: str = typer.Option(..., "--exchange")) -> None:
    typer.echo(f"{exchange}:{symbol.upper()}")
    typer.echo("Score: unavailable until feature snapshots exist")
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
pytest tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/altcoin_trend/scheduler.py src/altcoin_trend/cli.py tests/test_cli.py
git commit -m "feat: add run-once status and explain shell"
```

## Task 10: Daemon Entrypoint, systemd Service, and End-to-End Verification Hooks

**Files:**
- Create: `src/altcoin_trend/daemon.py`
- Create: `systemd/user/altcoin-trend.service`
- Modify: `README.md`
- Test: `tests/test_service_files.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_service_files.py`:

```python
from pathlib import Path


def test_systemd_service_points_to_independent_project():
    content = Path("systemd/user/altcoin-trend.service").read_text(encoding="utf-8")

    assert "WorkingDirectory=/home/tfisher/altcoin-trend-system" in content
    assert "EnvironmentFile=%h/.config/acts/acts.env" in content
    assert "Environment=PYTHONPATH=/home/tfisher/altcoin-trend-system/src" in content
    assert "ExecStart=/tmp/acts-venv/bin/python -m altcoin_trend.daemon" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_service_files.py -q
```

Expected: FAIL because the service file does not exist.

- [ ] **Step 3: Create daemon entrypoint**

Create `src/altcoin_trend/daemon.py`:

```python
import logging
import time

from altcoin_trend.config import load_settings
from altcoin_trend.scheduler import run_once_pipeline


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


def main() -> None:
    settings = load_settings()
    LOGGER.info("Starting altcoin trend daemon interval_seconds=%s", settings.signal_interval_seconds)
    while True:
        result = run_once_pipeline()
        LOGGER.info("Run-once completed status=%s message=%s", result.status, result.message)
        time.sleep(settings.signal_interval_seconds)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create service file**

Create `systemd/user/altcoin-trend.service`:

```ini
[Unit]
Description=Altcoin Trend System Daemon
After=default.target

[Service]
Type=simple
WorkingDirectory=/home/tfisher/altcoin-trend-system
EnvironmentFile=%h/.config/acts/acts.env
Environment=PYTHONPATH=/home/tfisher/altcoin-trend-system/src
ExecStart=/tmp/acts-venv/bin/python -m altcoin_trend.daemon
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

- [ ] **Step 5: Update README with service commands**

Append to `README.md`:

```markdown
## systemd User Service

```bash
mkdir -p ~/.config/systemd/user
cp systemd/user/altcoin-trend.service ~/.config/systemd/user/altcoin-trend.service
systemctl --user daemon-reload
systemctl --user enable --now altcoin-trend
systemctl --user status altcoin-trend --no-pager
```
```

- [ ] **Step 6: Run full test suite**

Run:

```bash
pytest -q
```

Expected: PASS.

- [ ] **Step 7: Verify CLI help**

Run:

```bash
python -m altcoin_trend.cli --help
```

Expected: command help lists `init-db`, `bootstrap`, `run-once`, `daemon`, `rank`, `status`, `alerts`, and `explain`.

- [ ] **Step 8: Commit**

Run:

```bash
git add src/altcoin_trend/daemon.py systemd/user/altcoin-trend.service README.md tests/test_service_files.py
git commit -m "feat: add daemon entrypoint and service file"
```

## Task 11: REST Row Parsing, HTTP Fetching, and Database Writes

**Files:**
- Modify: `src/altcoin_trend/exchanges/binance.py`
- Modify: `src/altcoin_trend/exchanges/bybit.py`
- Modify: `src/altcoin_trend/ingest/bootstrap.py`
- Modify: `src/altcoin_trend/db.py`
- Test: `tests/test_bootstrap.py`

- [ ] **Step 1: Add tests for HTTP response conversion without network**

Append to `tests/test_bootstrap.py`:

```python
from altcoin_trend.exchanges.binance import BinancePublicAdapter


def test_binance_rest_kline_parser_converts_rows():
    adapter = BinancePublicAdapter()
    rows = [
        [
            1710000000000,
            "100.0",
            "102.0",
            "99.5",
            "101.0",
            "1234.5",
            1710000059999,
            "124000.5",
            222,
            "800.0",
            "80500.0",
            "0",
        ]
    ]

    bars = adapter.parse_rest_klines("SOLUSDT", rows)

    assert len(bars) == 1
    assert bars[0].symbol == "SOLUSDT"
    assert bars[0].close == 101.0
    assert bars[0].is_closed is True


def test_bybit_rest_kline_parser_converts_rows():
    from altcoin_trend.exchanges.bybit import BybitPublicAdapter

    adapter = BybitPublicAdapter()
    rows = [["1710000000000", "100.0", "102.0", "99.5", "101.0", "1234.5", "124000.5"]]

    bars = adapter.parse_rest_klines("SOLUSDT", rows)

    assert len(bars) == 1
    assert bars[0].exchange == "bybit"
    assert bars[0].symbol == "SOLUSDT"
    assert bars[0].quote_volume == 124000.5
    assert bars[0].is_closed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_bootstrap.py::test_binance_rest_kline_parser_converts_rows -q
```

Expected: FAIL because `parse_rest_klines` is missing.

- [ ] **Step 3: Add REST row parsing and HTTP fetch methods**

Modify `src/altcoin_trend/exchanges/binance.py` imports:

```python
import httpx
```

Add to `BinancePublicAdapter`:

```python
    base_url = "https://fapi.binance.com"

    def parse_rest_klines(self, symbol: str, rows: list[list]) -> list[MarketBar1m]:
        return [
            MarketBar1m(
                exchange=self.exchange,
                symbol=symbol,
                ts=utc_from_ms(int(row[0])),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                quote_volume=float(row[7]),
                trade_count=int(row[8]) if row[8] is not None else None,
                taker_buy_base=float(row[9]) if row[9] is not None else None,
                taker_buy_quote=float(row[10]) if row[10] is not None else None,
                is_closed=True,
            )
            for row in rows
        ]

    def fetch_klines_1m(self, symbol: str, start_ms: int, end_ms: int) -> list[MarketBar1m]:
        response = httpx.get(
            f"{self.base_url}/fapi/v1/klines",
            params={
                "symbol": symbol,
                "interval": "1m",
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 1500,
            },
            timeout=20.0,
        )
        response.raise_for_status()
        return self.parse_rest_klines(symbol, response.json())
```

Modify `src/altcoin_trend/exchanges/bybit.py` imports:

```python
import httpx
```

Add to `BybitPublicAdapter`:

```python
    base_url = "https://api.bybit.com"

    def parse_rest_klines(self, symbol: str, rows: list[list[str]]) -> list[MarketBar1m]:
        return [
            MarketBar1m(
                exchange=self.exchange,
                symbol=symbol,
                ts=utc_from_ms(int(row[0])),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                quote_volume=float(row[6]),
                trade_count=None,
                taker_buy_base=None,
                taker_buy_quote=None,
                is_closed=True,
            )
            for row in rows
        ]

    def fetch_klines_1m(self, symbol: str, start_ms: int, end_ms: int) -> list[MarketBar1m]:
        response = httpx.get(
            f"{self.base_url}/v5/market/kline",
            params={
                "category": "linear",
                "symbol": symbol,
                "interval": "1",
                "start": start_ms,
                "end": end_ms,
                "limit": 1000,
            },
            timeout=20.0,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("result", {}).get("list", [])
        return self.parse_rest_klines(symbol, rows)
```

- [ ] **Step 4: Add explicit database writer interface**

Add to `src/altcoin_trend/db.py`:

```python
from collections.abc import Iterable


def insert_rows(engine: Engine, table_name: str, rows: Iterable[dict]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    columns = rows[0].keys()
    column_sql = ", ".join(columns)
    value_sql = ", ".join(f":{column}" for column in columns)
    statement = text(f"INSERT INTO {table_name} ({column_sql}) VALUES ({value_sql})")
    with engine.begin() as connection:
        connection.execute(statement, rows)
    return len(rows)
```

- [ ] **Step 5: Run targeted tests**

Run:

```bash
pytest tests/test_bootstrap.py tests/test_exchange_contracts.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/altcoin_trend/exchanges src/altcoin_trend/ingest src/altcoin_trend/db.py tests/test_bootstrap.py
git commit -m "feat: add REST row parsing and db inserts"
```

## Task 12: Final Verification and Documentation Pass

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-04-18-altcoin-trend-system-mvp-design.md` only if implementation decisions differ from the approved spec.

- [ ] **Step 1: Run full tests**

Run:

```bash
pytest -q
```

Expected: PASS.

- [ ] **Step 2: Verify package CLI**

Run:

```bash
python -m altcoin_trend.cli --help
```

Expected: CLI help exits 0 and lists all MVP commands.

- [ ] **Step 3: Verify editable install path**

Run:

```bash
python -c "import altcoin_trend, pathlib; print(pathlib.Path(altcoin_trend.__file__).resolve())"
```

Expected: output path starts with `/home/tfisher/altcoin-trend-system/src/altcoin_trend`.

- [ ] **Step 4: Verify git status**

Run:

```bash
git status --short
```

Expected: no uncommitted changes.

- [ ] **Step 5: Commit documentation changes if any**

If README or spec changed in this task, run:

```bash
git add README.md docs/superpowers/specs/2026-04-18-altcoin-trend-system-mvp-design.md
git commit -m "docs: update MVP usage notes"
```

Expected: a commit is created only when documentation changed.

## Spec Coverage Review

This plan maps the approved spec as follows:

```text
Independent project boundary: Tasks 1 and 10
PostgreSQL schemas: Task 2
Binance and Bybit adapter boundaries: Task 3
REST bootstrap and symbol filtering: Tasks 7 and 11
WebSocket stream naming and live acceptance: Task 8
Gap repair: Task 8
Local resampling and indicators: Task 4
Rule scoring and tiers: Task 5
Veto and explain output: Task 5
Signal transitions and alert dedupe: Task 6
Telegram delivery: Task 6
CLI commands: Tasks 1, 9, and 10
Daemon and systemd: Task 10
Testing and final verification: Tasks 1 through 12
```
