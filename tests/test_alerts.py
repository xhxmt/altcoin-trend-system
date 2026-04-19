from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from altcoin_trend.signals.alerts import (
    AlertCooldown,
    build_alert_event_rows,
    build_strong_alert_message,
    is_high_value_signal,
)
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


def test_alert_cooldown_isolates_exchange_symbol_and_alert_type_keys():
    cooldown = AlertCooldown(cooldown_seconds=3600)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    cooldown.record_sent("binance", "SOLUSDT", "strong_trend", now=now)

    assert cooldown.should_send("bybit", "SOLUSDT", "strong_trend", now=now) is True
    assert cooldown.should_send("binance", "ETHUSDT", "strong_trend", now=now) is True
    assert cooldown.should_send("binance", "SOLUSDT", "breakout_confirmed", now=now) is True


def test_alert_cooldown_rejects_naive_datetimes_for_should_send_and_record_sent():
    cooldown = AlertCooldown(cooldown_seconds=3600)
    naive_now = datetime(2026, 1, 1, 12, 0, 0)

    with pytest.raises(ValueError, match="timezone-aware"):
        cooldown.should_send("binance", "SOLUSDT", "strong_trend", now=naive_now)

    with pytest.raises(ValueError, match="timezone-aware"):
        cooldown.record_sent("binance", "SOLUSDT", "strong_trend", now=naive_now)


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


def test_build_strong_alert_message_strips_blank_items_to_none():
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
            "reasons": ["  ", "", "breakout confirmed"],
            "risks": ["", " "],
        }
    )

    assert "Reasons: breakout confirmed" in text
    assert "Risks: none" in text


def test_build_strong_alert_message_shows_na_for_missing_core_scores():
    text = build_strong_alert_message(
        {
            "exchange": "binance",
            "symbol": "SOLUSDT",
            "final_score": 91.25,
            "trend_score": None,
            "volume_breakout_score": None,
            "relative_strength_score": None,
            "derivatives_score": None,
            "quality_score": None,
        }
    )

    assert "Trend: n/a" in text
    assert "Volume breakout: n/a" in text
    assert "Relative strength: n/a" in text
    assert "Derivatives: n/a" in text
    assert "Quality: n/a" in text
    assert "Trend: None" not in text


def test_build_strong_alert_message_includes_derivatives_context_when_available():
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
            "oi_delta_1h": 12.5,
            "oi_delta_4h": -4.0,
            "funding_zscore": 1.8,
            "taker_buy_sell_ratio": 1.23,
        }
    )

    assert "OI delta 1h: 12.5" in text
    assert "OI delta 4h: -4.0" in text
    assert "Funding z-score: 1.8" in text
    assert "Taker buy/sell ratio: 1.23" in text


@pytest.mark.parametrize(
    "row",
    [
        {
            "tier": "strong",
            "trend_score": 74.0,
            "relative_strength_score": 70.0,
            "derivatives_score": 55.0,
            "quality_score": 80.0,
            "volume_breakout_score": 40.0,
            "veto_reason_codes": [],
        },
        {
            "tier": "strong",
            "trend_score": 75.0,
            "relative_strength_score": 69.0,
            "derivatives_score": 55.0,
            "quality_score": 80.0,
            "volume_breakout_score": 40.0,
            "veto_reason_codes": [],
        },
        {
            "tier": "strong",
            "trend_score": 75.0,
            "relative_strength_score": 70.0,
            "derivatives_score": 54.0,
            "quality_score": 80.0,
            "volume_breakout_score": 40.0,
            "veto_reason_codes": [],
        },
        {
            "tier": "strong",
            "trend_score": 75.0,
            "relative_strength_score": 70.0,
            "derivatives_score": 55.0,
            "quality_score": 79.0,
            "volume_breakout_score": 40.0,
            "veto_reason_codes": [],
        },
        {
            "tier": "strong",
            "trend_score": 75.0,
            "relative_strength_score": 70.0,
            "derivatives_score": 55.0,
            "quality_score": 80.0,
            "volume_breakout_score": 39.0,
            "veto_reason_codes": [],
        },
        {
            "tier": "rejected",
            "trend_score": 80.0,
            "relative_strength_score": 80.0,
            "derivatives_score": 80.0,
            "quality_score": 80.0,
            "volume_breakout_score": 80.0,
            "veto_reason_codes": [],
        },
        {
            "tier": "watchlist",
            "trend_score": 80.0,
            "relative_strength_score": 80.0,
            "derivatives_score": 80.0,
            "quality_score": 80.0,
            "volume_breakout_score": 80.0,
            "veto_reason_codes": ["liquidity"],
        },
    ],
)
def test_is_high_value_signal_rejects_non_qualifying_rows(row):
    assert is_high_value_signal(row) is False


def test_is_high_value_signal_accepts_complete_strong_and_watchlist_rows_for_mapping_and_object_rows():
    mapping_row = {
        "tier": "strong",
        "trend_score": 75.0,
        "relative_strength_score": 70.0,
        "derivatives_score": 55.0,
        "quality_score": 80.0,
        "volume_breakout_score": 40.0,
        "veto_reason_codes": (),
    }
    object_row = SimpleNamespace(
        tier="watchlist",
        trend_score=80.0,
        relative_strength_score=72.0,
        derivatives_score=60.0,
        quality_score=90.0,
        volume_breakout_score=45.0,
        veto_reason_codes=None,
    )

    assert is_high_value_signal(mapping_row) is True
    assert is_high_value_signal(object_row) is True


def test_build_alert_event_rows_blocks_positive_alerts_for_non_high_value_rows():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    positive_rows = [
        {
            "asset_id": 17,
            "exchange": "binance",
            "symbol": "SOLUSDT",
            "tier": "strong",
            "final_score": 88.4,
            "trend_score": 90.0,
            "volume_breakout_score": 80.0,
            "relative_strength_score": 50.0,
            "derivatives_score": 50.0,
            "quality_score": 100.0,
        },
        {
            "asset_id": 18,
            "exchange": "binance",
            "symbol": "SOLUSDT",
            "tier": "watchlist",
            "final_score": 88.4,
            "trend_score": 90.0,
            "volume_breakout_score": 80.0,
            "relative_strength_score": 50.0,
            "derivatives_score": 50.0,
            "quality_score": 100.0,
        },
    ]

    for rank_row in positive_rows:
        events = build_alert_event_rows([rank_row], recent_events=[], now=now, cooldown_seconds=3600)
        assert events == []


def test_build_alert_event_rows_keeps_risk_downgrade_alerts_even_when_row_is_not_high_value():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rank_row = {
        "asset_id": 19,
        "exchange": "binance",
        "symbol": "SOLUSDT",
        "tier": "watchlist",
        "final_score": 88.4,
        "trend_score": 90.0,
        "volume_breakout_score": 80.0,
        "relative_strength_score": 50.0,
        "derivatives_score": 50.0,
        "quality_score": 100.0,
    }
    recent_events = [
        {
            "asset_id": 19,
            "alert_type": "strong_trend",
            "ts": datetime(2025, 12, 31, tzinfo=timezone.utc),
            "payload": {"current_tier": "strong"},
        }
    ]

    events = build_alert_event_rows([rank_row], recent_events=recent_events, now=now, cooldown_seconds=3600)

    assert len(events) == 1
    assert events[0]["alert_type"] == "risk_downgrade"


def test_build_alert_event_rows_creates_strong_alert_and_suppresses_recent_duplicate():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rank_rows = [
        {
            "asset_id": 17,
            "exchange": "binance",
            "symbol": "SOLUSDT",
            "tier": "strong",
            "final_score": 88.4,
            "trend_score": 90.0,
            "volume_breakout_score": 80.0,
            "relative_strength_score": 75.0,
            "derivatives_score": 60.0,
            "quality_score": 100.0,
            "veto_reason_codes": [],
        }
    ]

    first_events = build_alert_event_rows(rank_rows, recent_events=[], now=now, cooldown_seconds=3600)

    assert len(first_events) == 1
    assert first_events[0]["asset_id"] == 17
    assert first_events[0]["alert_type"] == "strong_trend"
    assert first_events[0]["delivery_status"] == "pending"
    assert first_events[0]["payload"]["current_tier"] == "strong"
    assert "[STRONG] SOLUSDT Binance" in first_events[0]["message"]

    duplicate_events = build_alert_event_rows(
        rank_rows,
        recent_events=[first_events[0]],
        now=now + timedelta(minutes=10),
        cooldown_seconds=3600,
    )

    assert duplicate_events == []


def test_telegram_client_missing_config_returns_error_without_network():
    result = TelegramClient(bot_token="", chat_id="").send_message("hello")

    assert result.ok is False
    assert "missing" in result.error.lower()


def test_telegram_client_success_uses_expected_request_shape(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": {"message_id": 1}}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("altcoin_trend.signals.telegram.httpx.post", fake_post)

    result = TelegramClient(bot_token="bot-token", chat_id="chat-id", timeout_seconds=7.5).send_message("hello")

    assert result.ok is True
    assert result.error == ""
    assert captured == {
        "url": "https://api.telegram.org/botbot-token/sendMessage",
        "json": {"chat_id": "chat-id", "text": "hello"},
        "timeout": 7.5,
    }


def test_telegram_client_http_error_returns_failure(monkeypatch):
    def fake_post(url, json, timeout):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr("altcoin_trend.signals.telegram.httpx.post", fake_post)

    result = TelegramClient(bot_token="bot-token", chat_id="chat-id").send_message("hello")

    assert result.ok is False
    assert "boom" in result.error


def test_telegram_client_status_failure_returns_failure(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "bad status",
                request=httpx.Request("POST", "https://example.com"),
                response=httpx.Response(500),
            )

    def fake_post(url, json, timeout):
        return FakeResponse()

    monkeypatch.setattr("altcoin_trend.signals.telegram.httpx.post", fake_post)

    result = TelegramClient(bot_token="bot-token", chat_id="chat-id").send_message("hello")

    assert result.ok is False
    assert "bad status" in result.error


def test_telegram_client_api_error_response_returns_failure(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": False, "description": "rate limited"}

    monkeypatch.setattr("altcoin_trend.signals.telegram.httpx.post", lambda url, json, timeout: FakeResponse())

    result = TelegramClient(bot_token="bot-token", chat_id="chat-id").send_message("hello")

    assert result.ok is False
    assert result.error == "rate limited"


def test_telegram_client_invalid_json_returns_failure(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("invalid json")

    monkeypatch.setattr("altcoin_trend.signals.telegram.httpx.post", lambda url, json, timeout: FakeResponse())

    result = TelegramClient(bot_token="bot-token", chat_id="chat-id").send_message("hello")

    assert result.ok is False
    assert "invalid json" in result.error


def test_telegram_client_missing_config_does_not_attempt_post(monkeypatch):
    called = False

    def fake_post(url, json, timeout):
        nonlocal called
        called = True
        return SimpleNamespace()

    monkeypatch.setattr("altcoin_trend.signals.telegram.httpx.post", fake_post)

    result = TelegramClient(bot_token="", chat_id="chat-id").send_message("hello")

    assert result.ok is False
    assert called is False
