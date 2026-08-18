from datetime import datetime, timedelta, timezone

from app.research_swing_liquidity_api import (
    build_events_from_snapshots_and_klines,
    compact_closed_4h_candles,
)


def _candidate(side="long"):
    if side == "long":
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
    return {
        "symbol": "BTCUSDC",
        "side": "short",
        "expansion_score": 60,
        "direction_score": -40,
        "shortable": True,
        "trigger": {"timeframe": "4H", "price": 100, "requires_close": True},
        "entry_zone": {"low": 98, "high": 100},
        "stop": 104,
        "targets": [94, 90, 86],
    }


def _payload(*rows):
    return {"retCode": 0, "result": {"list": list(rows)}}


def _row(start_at, close):
    return [str(int(start_at.timestamp() * 1000)), "99", "101", "98", str(close), "1", "100"]


def test_compact_closed_4h_candles_excludes_open_bar_and_orders_oldest_first():
    now = datetime(2026, 8, 18, 20, tzinfo=timezone.utc)
    t0 = now - timedelta(hours=8)
    t1 = now - timedelta(hours=4)
    t2 = now
    payload = _payload(_row(t2, 103), _row(t1, 102), _row(t0, 101))

    candles = compact_closed_4h_candles(payload, now=now)

    assert [c["close"] for c in candles] == [101.0, 102.0]
    assert candles[0]["close_at"] == (t0 + timedelta(hours=4)).isoformat()
    assert candles[1]["close_at"] == (t1 + timedelta(hours=4)).isoformat()


def test_build_events_uses_durable_snapshot_and_closed_4h_trigger_without_outcomes():
    trigger_close = datetime(2026, 8, 18, 20, tzinfo=timezone.utc)
    start = trigger_close - timedelta(hours=4)
    snapshots = [
        {
            "captured_at": (trigger_close - timedelta(minutes=30)).isoformat(),
            "symbol": "BTCUSDC",
            "side": "long",
            "candidate": _candidate("long"),
        }
    ]
    klines = {"BTCUSDC": _payload(_row(start, 101))}

    events = build_events_from_snapshots_and_klines(snapshots, klines, now=trigger_close)

    assert len(events) == 1
    event = events[0]
    assert event["symbol"] == "BTCUSDC"
    assert event["side"] == "long"
    assert event["trigger_close_at"] == trigger_close.isoformat()
    assert event["pretrigger_snapshot_age_seconds"] == 1800.0
    assert event["research_only"] is True
    assert event["label_blind"] is True
    assert event["promotion_allowed"] is False
    assert "outcome" not in event and "net_r" not in event and "mfe_r" not in event


def test_build_events_keeps_distinct_trigger_bars_and_dedupes_repeated_hourly_snapshots():
    first_close = datetime(2026, 8, 18, 20, tzinfo=timezone.utc)
    second_close = first_close + timedelta(hours=4)
    snapshots = [
        {
            "captured_at": (first_close - timedelta(minutes=55)).isoformat(),
            "symbol": "BTCUSDC",
            "side": "long",
            "candidate": _candidate("long"),
        },
        {
            "captured_at": (first_close - timedelta(minutes=15)).isoformat(),
            "symbol": "BTCUSDC",
            "side": "long",
            "candidate": _candidate("long"),
        },
        {
            "captured_at": (second_close - timedelta(minutes=25)).isoformat(),
            "symbol": "BTCUSDC",
            "side": "long",
            "candidate": _candidate("long"),
        },
    ]
    klines = {
        "BTCUSDC": _payload(
            _row(first_close - timedelta(hours=4), 101),
            _row(second_close - timedelta(hours=4), 104),
        )
    }

    events = build_events_from_snapshots_and_klines(snapshots, klines, now=second_close)

    assert len(events) == 2
    assert [event["trigger_close_at"] for event in events] == [first_close.isoformat(), second_close.isoformat()]
    assert events[0]["pretrigger_captured_at"] == (first_close - timedelta(minutes=15)).isoformat()
    assert len({event["event_id"] for event in events}) == 2


def test_missing_kline_symbol_fails_closed_without_fabricating_event():
    t = datetime(2026, 8, 18, 20, tzinfo=timezone.utc)
    snapshots = [
        {
            "captured_at": (t - timedelta(minutes=30)).isoformat(),
            "symbol": "BTCUSDC",
            "side": "short",
            "candidate": _candidate("short"),
        }
    ]

    assert build_events_from_snapshots_and_klines(snapshots, {}, now=t) == []
