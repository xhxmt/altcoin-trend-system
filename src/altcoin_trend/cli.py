import typer

from altcoin_trend.config import load_settings
from altcoin_trend.db import build_engine, run_all_migrations

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
    typer.echo(
        f"Bootstrap requested lookback_days={lookback_days} "
        f"exchanges={','.join(settings.exchanges)} quote={settings.quote_asset}"
    )


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
