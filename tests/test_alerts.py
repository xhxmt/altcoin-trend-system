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


def test_build_alert_event_rows_creates_explosive_move_early_alert_independent_of_tier():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rank_rows = [
        {
            "asset_id": 21,
            "exchange": "binance",
            "symbol": "RAVEUSDT",
            "tier": "monitor",
            "final_score": 63.0,
            "trend_score": 67.0,
            "volume_breakout_score": 35.0,
            "relative_strength_score": 92.0,
            "derivatives_score": 31.0,
            "quality_score": 100.0,
            "return_1h_pct": 12.5,
            "return_4h_pct": 10.0,
            "return_24h_percentile": 0.98,
            "veto_reason_codes": [],
        }
    ]

    events = build_alert_event_rows(rank_rows, recent_events=[], now=now, cooldown_seconds=3600)

    assert len(events) == 1
    assert events[0]["alert_type"] == "explosive_move_early"
    assert "[EXPLOSIVE_MOVE_EARLY] RAVEUSDT Binance" in events[0]["message"]
    assert events[0]["payload"]["current_tier"] == "monitor"

    duplicate_events = build_alert_event_rows(
        rank_rows,
        recent_events=[events[0]],
        now=now + timedelta(minutes=10),
        cooldown_seconds=3600,
    )

    assert duplicate_events == []


def test_build_alert_event_rows_creates_ignition_extreme_event():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rank_row = {
        "asset_id": 21,
        "exchange": "binance",
        "symbol": "RAVEUSDT",
        "tier": "strong",
        "final_score": 63.0,
        "trend_score": 67.0,
        "volume_breakout_score": 35.0,
        "volume_impulse_score": 48.0,
        "relative_strength_score": 92.0,
        "derivatives_score": 31.0,
        "quality_score": 100.0,
        "return_1h_pct": 22.0,
        "return_24h_pct": 80.0,
        "continuation_grade": None,
        "ignition_grade": "EXTREME",
        "signal_priority": 3,
        "risk_flags": ["EXTREME_MOVE", "CHASE_RISK"],
        "chase_risk_score": 80.0,
        "actionability_score": 55.0,
        "cross_exchange_confirmed": True,
        "veto_reason_codes": [],
    }

    events = build_alert_event_rows([rank_row], recent_events=[], now=now, cooldown_seconds=3600)

    assert events[0]["alert_type"] == "ignition_extreme"
    assert "[IGNITION_EXTREME] RAVEUSDT" in events[0]["message"]
    assert events[0]["payload"]["priority"] == "P1"
    assert events[0]["payload"]["continuation_grade"] is None
    assert events[0]["payload"]["ignition_grade"] == "EXTREME"
    assert events[0]["payload"]["grades"] == {"continuation": None, "ignition": "EXTREME"}
    assert events[0]["payload"]["per_exchange_signals"] == {"binance": "IGNITION_EXTREME"}
    assert events[0]["payload"]["asset_ids"] == [21]
    assert events[0]["payload"]["exchanges"] == ["binance"]
    assert "binance=IGNITION_EXTREME" in events[0]["message"]


def test_build_alert_event_rows_dedupes_cross_exchange_ignition_by_symbol():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base = {
        "symbol": "RAVEUSDT",
        "tier": "strong",
        "final_score": 70.0,
        "trend_score": 60.0,
        "volume_breakout_score": 40.0,
        "volume_impulse_score": 50.0,
        "relative_strength_score": 95.0,
        "derivatives_score": 35.0,
        "quality_score": 100.0,
        "return_1h_pct": 24.0,
        "return_24h_pct": 90.0,
        "continuation_grade": None,
        "ignition_grade": "EXTREME",
        "signal_priority": 3,
        "risk_flags": ["EXTREME_MOVE"],
        "chase_risk_score": 80.0,
        "actionability_score": 60.0,
        "cross_exchange_confirmed": True,
        "veto_reason_codes": [],
    }
    rows = [
        dict(base, asset_id=101, exchange="binance"),
        dict(base, asset_id=202, exchange="bybit", actionability_score=65.0),
    ]

    events = build_alert_event_rows(rows, recent_events=[], now=now, cooldown_seconds=3600)

    ignition_events = [event for event in events if event["alert_type"] == "ignition_extreme"]
    assert len(ignition_events) == 1
    event = ignition_events[0]
    assert event["asset_id"] == 202
    assert event["payload"]["per_exchange_signals"] == {
        "binance": "IGNITION_EXTREME",
        "bybit": "IGNITION_EXTREME",
    }
    assert event["payload"]["asset_ids"] == [101, 202]
    assert event["payload"]["exchanges"] == ["binance", "bybit"]
    assert "binance=IGNITION_EXTREME" in event["message"]
    assert "bybit=IGNITION_EXTREME" in event["message"]


def test_build_alert_event_rows_suppresses_v2_duplicate_by_symbol_family_when_best_exchange_changes():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rank_row = {
        "asset_id": 202,
        "exchange": "bybit",
        "symbol": "RAVEUSDT",
        "tier": "strong",
        "final_score": 70.0,
        "trend_score": 60.0,
        "volume_breakout_score": 40.0,
        "volume_impulse_score": 50.0,
        "relative_strength_score": 95.0,
        "derivatives_score": 35.0,
        "quality_score": 100.0,
        "return_1h_pct": 24.0,
        "return_24h_pct": 90.0,
        "continuation_grade": None,
        "ignition_grade": "EXTREME",
        "signal_priority": 3,
        "risk_flags": ["EXTREME_MOVE"],
        "chase_risk_score": 80.0,
        "actionability_score": 65.0,
        "cross_exchange_confirmed": True,
        "veto_reason_codes": [],
    }
    recent_events = [
        {
            "asset_id": 101,
            "symbol": "RAVEUSDT",
            "alert_type": "ignition_extreme",
            "ts": now - timedelta(minutes=30),
            "payload": {"priority": "P1", "current_tier": "strong"},
        }
    ]

    events = build_alert_event_rows([rank_row], recent_events=recent_events, now=now, cooldown_seconds=3600)

    assert [event for event in events if event["alert_type"] == "ignition_extreme"] == []


def test_build_alert_event_rows_prefers_higher_severity_ignition_over_actionability():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base = {
        "symbol": "RAVEUSDT",
        "tier": "strong",
        "final_score": 70.0,
        "trend_score": 60.0,
        "volume_breakout_score": 40.0,
        "volume_impulse_score": 50.0,
        "relative_strength_score": 95.0,
        "derivatives_score": 35.0,
        "quality_score": 100.0,
        "return_1h_pct": 24.0,
        "return_24h_pct": 90.0,
        "continuation_grade": None,
        "signal_priority": 3,
        "risk_flags": ["EXTREME_MOVE"],
        "chase_risk_score": 80.0,
        "cross_exchange_confirmed": True,
        "veto_reason_codes": [],
    }
    rows = [
        dict(base, asset_id=208, exchange="binance", ignition_grade="EXTREME", actionability_score=40.0),
        dict(base, asset_id=209, exchange="bybit", ignition_grade="A", actionability_score=90.0),
    ]

    events = build_alert_event_rows(rows, recent_events=[], now=now, cooldown_seconds=3600)

    ignition_events = [event for event in events if event["alert_type"] in {"ignition_extreme", "ignition_detected"}]
    assert len(ignition_events) == 1
    assert ignition_events[0]["alert_type"] == "ignition_extreme"
    assert ignition_events[0]["asset_id"] == 208


def test_build_alert_event_rows_handles_null_actionability_and_exchange_in_v2_aggregation():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rank_row = {
        "asset_id": 203,
        "exchange": None,
        "symbol": "RAVEUSDT",
        "tier": "strong",
        "final_score": 70.0,
        "trend_score": 60.0,
        "volume_breakout_score": 40.0,
        "volume_impulse_score": 50.0,
        "relative_strength_score": 95.0,
        "derivatives_score": 35.0,
        "quality_score": 100.0,
        "return_1h_pct": 24.0,
        "return_24h_pct": 90.0,
        "continuation_grade": None,
        "ignition_grade": "EXTREME",
        "signal_priority": 3,
        "risk_flags": ["EXTREME_MOVE"],
        "chase_risk_score": 80.0,
        "actionability_score": None,
        "cross_exchange_confirmed": True,
        "veto_reason_codes": [],
    }

    events = build_alert_event_rows([rank_row], recent_events=[], now=now, cooldown_seconds=3600)

    assert len(events) == 1
    assert events[0]["alert_type"] == "ignition_extreme"
    assert events[0]["payload"]["exchange"] == "unknown"
    assert events[0]["payload"]["per_exchange_signals"] == {"unknown": "IGNITION_EXTREME"}


def test_build_alert_event_rows_labels_per_exchange_signals_by_signal_family():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rank_row = {
        "asset_id": 204,
        "exchange": "binance",
        "symbol": "SOLUSDT",
        "tier": "strong",
        "final_score": 88.4,
        "trend_score": 90.0,
        "volume_breakout_score": 80.0,
        "volume_impulse_score": 61.0,
        "relative_strength_score": 75.0,
        "derivatives_score": 60.0,
        "quality_score": 100.0,
        "return_1h_pct": 2.0,
        "return_24h_pct": 9.0,
        "continuation_grade": "A",
        "ignition_grade": "B",
        "signal_priority": 1,
        "risk_flags": [],
        "chase_risk_score": 20.0,
        "actionability_score": 89.0,
        "cross_exchange_confirmed": True,
        "veto_reason_codes": [],
    }

    events = build_alert_event_rows([rank_row], recent_events=[], now=now, cooldown_seconds=3600)

    assert len(events) == 1
    assert events[0]["alert_type"] == "continuation_confirmed"
    assert events[0]["payload"]["per_exchange_signals"] == {"binance": "CONTINUATION_A"}


def test_build_alert_event_rows_breaks_best_row_ties_by_priority_then_final_score():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base = {
        "symbol": "RAVEUSDT",
        "tier": "strong",
        "final_score": 70.0,
        "trend_score": 60.0,
        "volume_breakout_score": 40.0,
        "volume_impulse_score": 50.0,
        "relative_strength_score": 95.0,
        "derivatives_score": 35.0,
        "quality_score": 100.0,
        "return_1h_pct": 24.0,
        "return_24h_pct": 90.0,
        "continuation_grade": None,
        "ignition_grade": "EXTREME",
        "risk_flags": ["EXTREME_MOVE"],
        "chase_risk_score": 80.0,
        "actionability_score": 65.0,
        "cross_exchange_confirmed": True,
        "veto_reason_codes": [],
    }
    rows = [
        dict(base, asset_id=205, exchange="binance", signal_priority=1, final_score=72.0),
        dict(base, asset_id=206, exchange="bybit", signal_priority=2, final_score=71.0),
        dict(base, asset_id=207, exchange="okx", signal_priority=2, final_score=73.0),
    ]

    events = build_alert_event_rows(rows, recent_events=[], now=now, cooldown_seconds=3600)

    ignition_events = [event for event in events if event["alert_type"] == "ignition_extreme"]
    assert len(ignition_events) == 1
    assert ignition_events[0]["asset_id"] == 207


@pytest.mark.parametrize(("grade", "priority"), [("A", "P1"), ("B", "P2")])
def test_build_alert_event_rows_creates_continuation_confirmed_event_with_priority(grade, priority):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rank_row = {
        "asset_id": 22,
        "exchange": "binance",
        "symbol": "SOLUSDT",
        "tier": "strong",
        "final_score": 88.4,
        "trend_score": 90.0,
        "volume_breakout_score": 80.0,
        "volume_impulse_score": 61.0,
        "relative_strength_score": 75.0,
        "derivatives_score": 60.0,
        "quality_score": 100.0,
        "return_1h_pct": 2.0,
        "return_24h_pct": 9.0,
        "continuation_grade": grade,
        "ignition_grade": None,
        "signal_priority": 1,
        "risk_flags": [],
        "chase_risk_score": 20.0,
        "actionability_score": 89.0,
        "cross_exchange_confirmed": True,
        "veto_reason_codes": [],
    }

    events = build_alert_event_rows([rank_row], recent_events=[], now=now, cooldown_seconds=3600)

    assert len(events) == 1
    assert events[0]["alert_type"] == "continuation_confirmed"
    assert f"[CONTINUATION_{grade}] SOLUSDT" in events[0]["message"]
    assert events[0]["payload"]["priority"] == priority
    assert events[0]["payload"]["continuation_grade"] == grade
    assert events[0]["payload"]["ignition_grade"] is None
    assert events[0]["payload"]["grades"] == {"continuation": grade, "ignition": None}


@pytest.mark.parametrize(("grade", "priority"), [("A", "P2"), ("B", "P3")])
def test_build_alert_event_rows_creates_ignition_detected_event_with_priority(grade, priority):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rank_row = {
        "asset_id": 23,
        "exchange": "binance",
        "symbol": "RAYUSDT",
        "tier": "watchlist",
        "final_score": 76.0,
        "trend_score": 75.0,
        "volume_breakout_score": 50.0,
        "volume_impulse_score": 70.0,
        "relative_strength_score": 84.0,
        "derivatives_score": 59.0,
        "quality_score": 90.0,
        "return_1h_pct": 8.0,
        "return_24h_pct": 25.0,
        "continuation_grade": None,
        "ignition_grade": grade,
        "signal_priority": 2,
        "risk_flags": [],
        "chase_risk_score": 40.0,
        "actionability_score": 72.0,
        "cross_exchange_confirmed": False,
        "veto_reason_codes": [],
    }

    events = build_alert_event_rows([rank_row], recent_events=[], now=now, cooldown_seconds=3600)

    assert len(events) == 1
    assert events[0]["alert_type"] == "ignition_detected"
    assert f"[IGNITION_{grade}] RAYUSDT" in events[0]["message"]
    assert events[0]["payload"]["priority"] == priority


def test_build_alert_event_rows_creates_exhaustion_risk_without_grade_when_chase_risk_is_high():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rank_row = {
        "asset_id": 24,
        "exchange": "binance",
        "symbol": "HOTUSDT",
        "tier": "watchlist",
        "final_score": 71.0,
        "trend_score": 65.0,
        "volume_breakout_score": 30.0,
        "volume_impulse_score": 32.0,
        "relative_strength_score": 78.0,
        "derivatives_score": 45.0,
        "quality_score": 85.0,
        "return_1h_pct": 10.0,
        "return_24h_pct": 42.0,
        "continuation_grade": None,
        "ignition_grade": None,
        "signal_priority": 3,
        "risk_flags": ["CHASE_RISK"],
        "chase_risk_score": 80.0,
        "actionability_score": 38.0,
        "cross_exchange_confirmed": False,
        "veto_reason_codes": [],
    }

    events = build_alert_event_rows([rank_row], recent_events=[], now=now, cooldown_seconds=3600)

    assert len(events) == 1
    assert events[0]["alert_type"] == "exhaustion_risk"
    assert "[EXHAUSTION_RISK] HOTUSDT" in events[0]["message"]
    assert events[0]["payload"]["priority"] == "P2"


def test_build_alert_event_rows_prefers_v2_event_over_duplicate_legacy_transition():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rank_row = {
        "asset_id": 25,
        "exchange": "binance",
        "symbol": "ADAUSDT",
        "tier": "strong",
        "final_score": 88.4,
        "trend_score": 90.0,
        "volume_breakout_score": 80.0,
        "volume_impulse_score": 63.0,
        "relative_strength_score": 75.0,
        "derivatives_score": 60.0,
        "quality_score": 100.0,
        "return_1h_pct": 1.5,
        "return_24h_pct": 7.0,
        "continuation_grade": "A",
        "ignition_grade": None,
        "signal_priority": 1,
        "risk_flags": [],
        "chase_risk_score": 15.0,
        "actionability_score": 94.0,
        "cross_exchange_confirmed": True,
        "veto_reason_codes": [],
    }

    events = build_alert_event_rows([rank_row], recent_events=[], now=now, cooldown_seconds=3600)

    assert len(events) == 1
    assert events[0]["alert_type"] == "continuation_confirmed"
    assert events[0]["alert_type"] not in {"strong_trend", "breakout_confirmed"}


def test_build_alert_event_rows_keeps_explosive_alert_with_v2_and_skips_legacy_transition():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rank_row = {
        "asset_id": 28,
        "exchange": "binance",
        "symbol": "RAVEUSDT",
        "tier": "strong",
        "final_score": 91.0,
        "trend_score": 90.0,
        "volume_breakout_score": 80.0,
        "volume_impulse_score": 88.0,
        "relative_strength_score": 92.0,
        "derivatives_score": 60.0,
        "quality_score": 100.0,
        "return_1h_pct": 22.0,
        "return_4h_pct": 28.0,
        "return_24h_pct": 80.0,
        "return_24h_percentile": 0.98,
        "continuation_grade": None,
        "ignition_grade": "EXTREME",
        "signal_priority": 3,
        "risk_flags": ["EXTREME_MOVE", "CHASE_RISK"],
        "chase_risk_score": 80.0,
        "actionability_score": 55.0,
        "cross_exchange_confirmed": True,
        "veto_reason_codes": [],
    }

    events = build_alert_event_rows([rank_row], recent_events=[], now=now, cooldown_seconds=3600)

    alert_types = [event["alert_type"] for event in events]
    assert alert_types == ["ignition_extreme", "explosive_move_early"]
    assert "strong_trend" not in alert_types
    assert "breakout_confirmed" not in alert_types


def test_build_alert_event_rows_keeps_legacy_alert_for_strong_row_without_v2_grades():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rank_row = {
        "asset_id": 26,
        "exchange": "binance",
        "symbol": "DOGEUSDT",
        "tier": "strong",
        "final_score": 88.4,
        "trend_score": 90.0,
        "volume_breakout_score": 80.0,
        "relative_strength_score": 75.0,
        "derivatives_score": 60.0,
        "quality_score": 100.0,
        "continuation_grade": None,
        "ignition_grade": None,
        "veto_reason_codes": [],
    }

    events = build_alert_event_rows([rank_row], recent_events=[], now=now, cooldown_seconds=3600)

    assert len(events) == 1
    assert events[0]["alert_type"] == "strong_trend"
    assert "[STRONG] DOGEUSDT Binance" in events[0]["message"]


def test_build_alert_event_rows_uses_priority_cooldown_for_p3_ignition_detected():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rank_row = {
        "asset_id": 27,
        "exchange": "binance",
        "symbol": "ATOMUSDT",
        "tier": "watchlist",
        "final_score": 76.0,
        "trend_score": 75.0,
        "volume_breakout_score": 50.0,
        "volume_impulse_score": 70.0,
        "relative_strength_score": 84.0,
        "derivatives_score": 59.0,
        "quality_score": 90.0,
        "return_1h_pct": 8.0,
        "return_24h_pct": 25.0,
        "continuation_grade": None,
        "ignition_grade": "B",
        "signal_priority": 3,
        "risk_flags": [],
        "chase_risk_score": 40.0,
        "actionability_score": 72.0,
        "cross_exchange_confirmed": False,
        "veto_reason_codes": [],
    }
    recent_event = {
        "asset_id": 27,
        "alert_type": "ignition_detected",
        "ts": now - timedelta(hours=3, minutes=59),
        "payload": {"priority": "P3", "current_tier": "watchlist"},
    }

    suppressed_events = build_alert_event_rows([rank_row], recent_events=[recent_event], now=now, cooldown_seconds=3600)
    allowed_events = build_alert_event_rows(
        [rank_row],
        recent_events=[{**recent_event, "ts": now - timedelta(hours=4)}],
        now=now,
        cooldown_seconds=3600,
    )

    assert suppressed_events == []
    assert len(allowed_events) == 1
    assert allowed_events[0]["alert_type"] == "ignition_detected"
    assert allowed_events[0]["payload"]["priority"] == "P3"


def test_build_alert_event_rows_uses_newest_recent_event_for_duplicate_cooldown_keys():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rank_row = {
        "asset_id": 29,
        "exchange": "binance",
        "symbol": "LINKUSDT",
        "tier": "watchlist",
        "final_score": 76.0,
        "trend_score": 75.0,
        "volume_breakout_score": 50.0,
        "volume_impulse_score": 70.0,
        "relative_strength_score": 84.0,
        "derivatives_score": 59.0,
        "quality_score": 90.0,
        "return_1h_pct": 8.0,
        "return_24h_pct": 25.0,
        "continuation_grade": None,
        "ignition_grade": "B",
        "signal_priority": 3,
        "risk_flags": [],
        "chase_risk_score": 40.0,
        "actionability_score": 72.0,
        "cross_exchange_confirmed": False,
        "veto_reason_codes": [],
    }
    recent_events = [
        {
            "asset_id": 29,
            "alert_type": "ignition_detected",
            "ts": now - timedelta(hours=1),
            "payload": {"priority": "P3", "current_tier": "watchlist"},
        },
        {
            "asset_id": 29,
            "alert_type": "ignition_detected",
            "ts": now - timedelta(hours=5),
            "payload": {"priority": "P3", "current_tier": "watchlist"},
        },
    ]

    events = build_alert_event_rows([rank_row], recent_events=recent_events, now=now, cooldown_seconds=3600)

    assert events == []


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
