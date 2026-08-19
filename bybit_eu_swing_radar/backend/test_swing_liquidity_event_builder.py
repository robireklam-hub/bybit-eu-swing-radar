from datetime import datetime, timedelta, timezone

from research.swing_liquidity_event_builder import (
    build_first_trigger_event,
    build_trigger_events,
    select_pretrigger_snapshot,
)


def _candidate():
    return {
        "symbol": "BTCUSDC",
        "side": "long",
        "expansion_score": 60,
        "direction_score": 40,
        "shortable": False,
        "trigger": {"timeframe": "4H", "price": 100, "requires_close": True},
        "entry_zone": {"low": 100, "high": 102},
        "stop": 95,
        "targets": [110, 116, 122],
    }


def test_selects_latest_strictly_pretrigger_snapshot_within_90m():
    t = datetime(2026, 8, 18, 16, tzinfo=timezone.utc)
    rows = [
        {"symbol": "BTCUSDC", "side": "long", "captured_at": t - timedelta(minutes=80)},
        {"symbol": "BTCUSDC", "side": "long", "captured_at": t - timedelta(minutes=20)},
        {"symbol": "BTCUSDC", "side": "long", "captured_at": t},
    ]
    chosen = select_pretrigger_snapshot(rows, symbol="BTCUSDC", side="long", trigger_close_at=t)
    assert chosen["captured_at"] == t - timedelta(minutes=20)


def test_builds_first_chronological_trigger_only_and_is_label_blind():
    t = datetime(2026, 8, 18, 16, tzinfo=timezone.utc)
    snapshots = [
        {"symbol": "BTCUSDC", "side": "long", "captured_at": t - timedelta(minutes=30), "candidate": _candidate()},
        {"symbol": "BTCUSDC", "side": "long", "captured_at": t + timedelta(hours=4, minutes=-30), "candidate": _candidate()},
    ]
    candles = [
        {"start_at": t - timedelta(hours=4), "close_at": t, "close": 99.5},
        {"start_at": t, "close_at": t + timedelta(hours=4), "close": 101},
        {"start_at": t + timedelta(hours=4), "close_at": t + timedelta(hours=8), "close": 105},
    ]
    event = build_first_trigger_event(snapshots, candles, symbol="BTCUSDC", side="long")
    assert event["trigger_close_at"] == (t + timedelta(hours=4)).isoformat()
    assert event["research_only"] is True
    assert event["label_blind"] is True
    assert event["promotion_allowed"] is False
    assert "net_r" not in event and "outcome" not in event


def test_builds_distinct_trigger_bar_events_and_dedupes_hourly_covariates():
    t0 = datetime(2026, 8, 18, 16, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=4)
    t2 = t1 + timedelta(hours=4)
    snapshots = [
        {"symbol": "BTCUSDC", "side": "long", "captured_at": t1 - timedelta(minutes=50), "candidate": _candidate()},
        {"symbol": "BTCUSDC", "side": "long", "captured_at": t1 - timedelta(minutes=20), "candidate": _candidate()},
        {"symbol": "BTCUSDC", "side": "long", "captured_at": t2 - timedelta(minutes=30), "candidate": _candidate()},
    ]
    candles = [
        {"start_at": t0, "close_at": t1, "close": 101},
        {"start_at": t1, "close_at": t2, "close": 103},
    ]

    events = build_trigger_events(snapshots, candles, symbol="BTCUSDC", side="long")

    assert [event["trigger_close_at"] for event in events] == [t1.isoformat(), t2.isoformat()]
    assert events[0]["pretrigger_captured_at"] == (t1 - timedelta(minutes=20)).isoformat()
    assert events[1]["pretrigger_captured_at"] == (t2 - timedelta(minutes=30)).isoformat()
    assert len({event["event_id"] for event in events}) == 2
    assert all("net_r" not in event and "outcome" not in event for event in events)


def test_does_not_reuse_old_snapshot_for_later_trigger_bar():
    first_close = datetime(2026, 8, 18, 20, tzinfo=timezone.utc)
    later_close = first_close + timedelta(hours=4)
    snapshots = [
        {"symbol": "BTCUSDC", "side": "long", "captured_at": first_close - timedelta(minutes=20), "candidate": _candidate()},
    ]
    candles = [
        {"start_at": first_close - timedelta(hours=4), "close_at": first_close, "close": 101},
        {"start_at": later_close - timedelta(hours=4), "close_at": later_close, "close": 103},
    ]
    events = build_trigger_events(snapshots, candles, symbol="BTCUSDC", side="long")
    assert [event["trigger_close_at"] for event in events] == [first_close.isoformat()]


def test_returns_none_when_only_stale_or_posttrigger_snapshot_exists():
    t = datetime(2026, 8, 18, 16, tzinfo=timezone.utc)
    snapshots = [
        {"symbol": "BTCUSDC", "side": "long", "captured_at": t - timedelta(minutes=91), "candidate": _candidate()},
        {"symbol": "BTCUSDC", "side": "long", "captured_at": t + timedelta(seconds=1), "candidate": _candidate()},
    ]
    candles = [{"start_at": t - timedelta(hours=4), "close_at": t, "close": 101}]
    assert build_first_trigger_event(snapshots, candles, symbol="BTCUSDC", side="long") is None
    assert build_trigger_events(snapshots, candles, symbol="BTCUSDC", side="long") == []
