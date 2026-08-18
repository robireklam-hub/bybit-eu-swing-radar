from datetime import datetime, timedelta, timezone

from research.swing_liquidity_event_builder import build_first_trigger_event, select_pretrigger_snapshot


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


def test_returns_none_when_only_stale_or_posttrigger_snapshot_exists():
    t = datetime(2026, 8, 18, 16, tzinfo=timezone.utc)
    snapshots = [
        {"symbol": "BTCUSDC", "side": "long", "captured_at": t - timedelta(minutes=91), "candidate": _candidate()},
        {"symbol": "BTCUSDC", "side": "long", "captured_at": t + timedelta(seconds=1), "candidate": _candidate()},
    ]
    candles = [{"start_at": t - timedelta(hours=4), "close_at": t, "close": 101}]
    assert build_first_trigger_event(snapshots, candles, symbol="BTCUSDC", side="long") is None
