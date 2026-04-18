from datetime import datetime, timezone

import typer

from altcoin_trend.config import load_settings
from altcoin_trend.daemon import main as daemon_main
from altcoin_trend.db import build_engine, run_all_migrations
from altcoin_trend.exchanges.binance import BinancePublicAdapter
from altcoin_trend.exchanges.bybit import BybitPublicAdapter
from altcoin_trend.ingest.bootstrap import bootstrap_exchange
from altcoin_trend.scheduler import run_once_pipeline

app = typer.Typer(help="Altcoin trend system CLI")


@app.callback()
def main() -> None:
    """Register a root callback so Typer keeps subcommand mode."""


@app.command("init-db")
def init_db() -> None:
    settings = load_settings()
    engine = build_engine(settings)
    run_all_migrations(engine)
    typer.echo("Initialized altcoin trend database schema")


@app.command("bootstrap")
def bootstrap(lookback_days: int = typer.Option(90, "--lookback-days", min=1)) -> None:
    settings = load_settings()
    engine = build_engine(settings)
    now = datetime.now(timezone.utc)
    total_bars = 0
    for exchange in settings.exchanges:
        if exchange == "binance":
            adapter = BinancePublicAdapter()
        elif exchange == "bybit":
            adapter = BybitPublicAdapter()
        else:
            raise typer.BadParameter(f"Unsupported exchange: {exchange}")
        result = bootstrap_exchange(adapter=adapter, engine=engine, settings=settings, lookback_days=lookback_days, now=now)
        total_bars += result.bars_written
        typer.echo(
            f"Bootstrap {result.exchange} instruments={result.instruments_selected} "
            f"bars_written={result.bars_written}"
        )
    typer.echo(f"Bootstrap completed exchanges={len(settings.exchanges)} bars_written={total_bars}")


@app.command("run-once")
def run_once() -> None:
    result = run_once_pipeline()
    typer.echo(f"Run once status={result.status} message={result.message}")


@app.command("daemon")
def daemon() -> None:
    daemon_main()


@app.command("rank")
def rank(
    limit: int = typer.Option(30, "--limit", min=1),
    exchange: str | None = typer.Option(None, "--exchange"),
) -> None:
    scope = exchange or "all"
    typer.echo(f"Rank requested for scope={scope} limit={limit}")


@app.command("status")
def status() -> None:
    settings = load_settings()
    typer.echo(
        f"Status: configured exchanges={','.join(settings.exchanges)} "
        f"interval={settings.signal_interval_seconds}s"
    )


@app.command("alerts")
def alerts(since: str = typer.Option("24h", "--since")) -> None:
    typer.echo(f"Alerts requested since {since}")


@app.command("explain")
def explain(symbol: str, exchange: str = typer.Option(..., "--exchange")) -> None:
    typer.echo(f"{exchange}:{symbol.upper()}")
    typer.echo("Score: unavailable until feature snapshots exist")


if __name__ == "__main__":
    app()
