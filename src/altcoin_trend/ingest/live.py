from altcoin_trend.models import MarketBar1m


def accept_closed_bar(bar: MarketBar1m) -> bool:
    return bar.is_closed
