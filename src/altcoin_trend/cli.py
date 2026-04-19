from datetime import datetime, timezone

import typer

from altcoin_trend.backtest import parse_horizons, run_signal_backtest
from altcoin_trend.config import load_settings
from altcoin_trend.daemon import main as daemon_main
from altcoin_trend.db import build_engine, run_all_migrations
from altcoin_trend.exchanges.binance import BinancePublicAdapter
from altcoin_trend.exchanges.bybit import BybitPublicAdapter
from altcoin_trend.ingest.bootstrap import bootstrap_exchange
from altcoin_trend.ingest.derivatives import bootstrap_derivatives
from altcoin_trend.scheduler import load_explain_row, load_rank_rows, process_alerts, run_once_pipeline
from altcoin_trend.signals.explain import build_explain_text
from altcoin_trend.signals.ranking import aggregate_rank_rows_by_symbol
from altcoin_trend.signals.telegram import TelegramClient

app = typer.Typer(help="Altcoin trend system CLI")


@app.callback()
def main() -> None:
    """Register a root callback so Typer keeps subcommand mode."""


def _selection_mode_text(settings) -> str:
    allowlist_count = len(settings.allowlist_symbols)
    blocklist_count = len(settings.blocklist_symbols)
    mode = "full-market" if allowlist_count == 0 else "allowlist"
    return f"selection mode={mode} allowlist={allowlist_count} blocklist={blocklist_count}"


def _parse_iso_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"Invalid ISO datetime: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return " ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _parse_horizons_option(value: str):
    try:
        return parse_horizons(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


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
    typer.echo(f"Bootstrap {_selection_mode_text(settings)}")
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


@app.command("bootstrap-derivatives")
def bootstrap_derivatives_command(lookback_days: int = typer.Option(31, "--lookback-days", min=1)) -> None:
    settings = load_settings()
    engine = build_engine(settings)
    now = datetime.now(timezone.utc)
    typer.echo(f"Bootstrap derivatives {_selection_mode_text(settings)}")
    for exchange in settings.exchanges:
        if exchange == "binance":
            adapter = BinancePublicAdapter()
        elif exchange == "bybit":
            adapter = BybitPublicAdapter()
        else:
            raise typer.BadParameter(f"Unsupported exchange: {exchange}")
        updates = bootstrap_derivatives(adapter=adapter, engine=engine, settings=settings, lookback_days=lookback_days, now=now)
        typer.echo(f"Derivatives bootstrap {exchange} updates={updates}")


@app.command("run-once")
def run_once() -> None:
    settings = load_settings()
    engine = build_engine(settings)
    result = run_once_pipeline(engine=engine)
    typer.echo(f"Run once status={result.status} message={result.message}")


@app.command("daemon")
def daemon() -> None:
    daemon_main()


@app.command("rank")
def rank(
    limit: int = typer.Option(30, "--limit", min=1),
    exchange: str | None = typer.Option(None, "--exchange"),
    aggregate_symbols: bool = typer.Option(False, "--aggregate-symbols"),
) -> None:
    scope = exchange or "all"
    settings = load_settings()
    engine = build_engine(settings)
    load_limit = limit
    if aggregate_symbols:
        load_limit = max(limit * max(1, len(settings.exchanges)), limit)
    rows = load_rank_rows(engine, rank_scope=scope, limit=load_limit)
    if aggregate_symbols:
        rows = aggregate_rank_rows_by_symbol(rows)
        rows = rows[:limit]
    if not rows:
        typer.echo(f"No rank snapshot found for scope={scope}")
        return
    typer.echo(f"Rank snapshot scope={scope} limit={limit} aggregate_symbols={aggregate_symbols}")
    for row in rows:
        row_exchange = row.get("exchange") or "unknown"
        line = f"{row['rank']}. {row_exchange}:{row['symbol']} score={row['final_score']} tier={row['tier']}"
        if aggregate_symbols:
            line += f" exchanges={row['exchange_count']} avg_score={row['average_score']}"
        typer.echo(line)


@app.command("status")
def status() -> None:
    settings = load_settings()
    typer.echo(
        f"Status: configured exchanges={','.join(settings.exchanges)} "
        f"interval={settings.signal_interval_seconds}s"
    )


@app.command("alerts")
def alerts(since: str = typer.Option("24h", "--since")) -> None:
    settings = load_settings()
    engine = build_engine(settings)
    telegram_client = None
    if settings.telegram_bot_token and settings.telegram_chat_id:
        telegram_client = TelegramClient(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
    inserted, sent = process_alerts(
        engine=engine,
        now=datetime.now(timezone.utc),
        cooldown_seconds=settings.alert_cooldown_seconds,
        telegram_client=telegram_client,
    )
    typer.echo(f"Alerts processed inserted={inserted} sent={sent} since={since}")


@app.command("backtest")
def backtest(
    from_ts: str = typer.Option(..., "--from"),
    to_ts: str = typer.Option(..., "--to"),
    min_score: float = typer.Option(60.0, "--min-score"),
    horizons: str = typer.Option("1h,4h,24h,1d", "--horizons"),
    high_value_only: bool = typer.Option(False, "--high-value-only"),
    limit: int = typer.Option(10, "--limit", min=1),
) -> None:
    start = _parse_iso_datetime(from_ts)
    end = _parse_iso_datetime(to_ts)
    if start >= end:
        raise typer.BadParameter("--from must be earlier than --to")

    settings = load_settings()
    engine = build_engine(settings)
    summary = run_signal_backtest(
        engine=engine,
        start=start,
        end=end,
        min_score=min_score,
        horizons=_parse_horizons_option(horizons),
        high_value_only=high_value_only,
        limit=limit,
    )
    if summary.signal_count == 0:
        typer.echo("No signals found for requested filters")
        return

    typer.echo(
        f"Backtest from={start.isoformat()} to={end.isoformat()} "
        f"signals={summary.signal_count} average_score={summary.average_score}"
    )
    typer.echo(f"Tier counts: {_format_counts(summary.tier_counts)}")
    typer.echo(f"Exchange counts: {_format_counts(summary.exchange_counts)}")
    for horizon, stats in summary.horizon_stats.items():
        typer.echo(
            f"{horizon} avg_return={stats.avg_return * 100:.2f}% "
            f"win_rate={stats.win_rate:.2f}% observations={stats.observations}"
        )
    typer.echo("Top signals:")
    for index, signal in enumerate(summary.top_signals, start=1):
        typer.echo(
            f"{index}. {signal.get('exchange', 'unknown')}:{signal.get('symbol', 'unknown')} "
            f"score={signal.get('final_score')} tier={signal.get('tier', 'rejected')}"
        )


@app.command("explain")
def explain(symbol: str, exchange: str = typer.Option(..., "--exchange")) -> None:
    settings = load_settings()
    engine = build_engine(settings)
    row = load_explain_row(engine, symbol=symbol, exchange=exchange)
    if row is None:
        typer.echo(f"{exchange}:{symbol.upper()}")
        typer.echo("Score: unavailable until feature snapshots exist")
        return
    typer.echo(build_explain_text(row))


if __name__ == "__main__":
    app()
