# Altcoin Trend System

Minute-to-hour USDT perpetual trend scanner for Binance USD-M and Bybit linear markets.

The first version stores data in PostgreSQL/TimescaleDB-compatible tables, computes rule-based trend rankings, and sends Telegram alerts. It does not trade.

## Configuration

```bash
mkdir -p ~/.config/acts
cp config/acts.env.example ~/.config/acts/acts.env
```

`AppSettings` loads `~/.config/acts/acts.env` automatically, and direct `ACTS_*`
environment variables still override values from that file.

## CLI

```bash
acts --help
acts init-db
acts bootstrap --lookback-days 90
acts run-once
acts rank --limit 30
acts explain SOLUSDT --exchange binance
```

## systemd User Service

```bash
mkdir -p ~/.config/systemd/user
cp systemd/user/altcoin-trend.service ~/.config/systemd/user/altcoin-trend.service
systemctl --user daemon-reload
systemctl --user enable --now altcoin-trend
systemctl --user status altcoin-trend --no-pager
```
