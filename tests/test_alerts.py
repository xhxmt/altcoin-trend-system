from datetime import datetime, timedelta, timezone

from altcoin_trend.signals.alerts import AlertCooldown, build_strong_alert_message
from altcoin_trend.signals.telegram import TelegramClient


def test_alert_cooldown_suppresses_duplicates_within_window_and_allows_after_expiry():
    cooldown = AlertCooldown(cooldown_seconds=3600)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    assert cooldown.should_send("binance", "SOLUSDT", "strong_trend", now=now) is True
    cooldown.record_sent("binance", "SOLUSDT", "strong_trend", now=now)

    assert cooldown.should_send(
        "binance",
        "SOLUSDT",
        "strong_trend",
        now=now + timedelta(minutes=30),
    ) is False
    assert cooldown.should_send(
        "binance",
        "SOLUSDT",
        "strong_trend",
        now=now + timedelta(hours=1, seconds=1),
    ) is True


def test_build_strong_alert_message_includes_header_reasons_and_risks():
    text = build_strong_alert_message(
        {
            "exchange": "binance",
            "symbol": "SOLUSDT",
            "final_score": 91.25,
            "trend_score": 95.0,
            "volume_breakout_score": 90.0,
            "relative_strength_score": 88.0,
            "derivatives_score": 77.0,
            "quality_score": 84.0,
            "reasons": ["breakout confirmed", "oi expansion"],
            "risks": ["funding heat", "low liquidity"],
        }
    )

    assert "[STRONG] SOLUSDT Binance" in text
    assert "Reasons:" in text
    assert "Risks:" in text
    assert "breakout confirmed" in text
    assert "funding heat" in text


def test_telegram_client_missing_config_returns_error_without_network():
    result = TelegramClient(bot_token="", chat_id="").send_message("hello")

    assert result.ok is False
    assert "missing" in result.error.lower()
