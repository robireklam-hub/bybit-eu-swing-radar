from datetime import datetime, timedelta, timezone

from research_breakout_continuation_v5 import build_breakout_report, replay_symbol_breakouts
from worker import Bar


def _bar(start_ms: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> Bar:
    return Bar(start_ms=start_ms, open=o, high=h, low=l, close=c, volume=v, turnover=v * c)


def test_breakout_signal_uses_prior_channel_and_future_only_for_outcome():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t0 = int(start.timestamp() * 1000)
    bars = []
    price = 100.0
    for i in range(100):
        bars.append(_bar(t0 + i * 300_000, price, price + 0.4, price - 0.4, price, 100.0))
    # Break above every prior channel high with strong volume/close.
    bars.append(_bar(t0 + 100 * 300_000, 100.0, 103.0, 99.9, 102.9, 300.0))
    # Future path reaches the modeled target before the stop.
    for i in range(101, 121):
        bars.append(_bar(t0 + i * 300_000, 103.0, 110.0, 102.0, 109.0, 100.0))

    events = replay_symbol_breakouts(
        symbol="BTCUSDC",
        bars=bars,
        start_at=start,
        end_at=start + timedelta(hours=12),
        development_end_at=start + timedelta(days=120),
    )
    long_events = [row for row in events if row["side"] == "long"]
    assert long_events
    event = long_events[0]
    assert event["opened_at"] == start + timedelta(minutes=505)
    assert event["volume_confirmed"] is True
    assert event["strong_close"] is True


def test_report_selects_only_train_and_requires_positive_internal_holdout():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    development_end = start + timedelta(days=120)
    rows = []
    for i in range(180):
        rows.append({
            "opened_at": start + timedelta(hours=i * 10),
            "dataset_split": "DEVELOPMENT",
            "net_r": -0.2 if i % 3 == 0 else 0.5,
            "volume_confirmed": True,
            "strong_close": True,
        })
    for i in range(60):
        rows.append({
            "opened_at": start + timedelta(days=95, hours=i * 8),
            "dataset_split": "DEVELOPMENT",
            "net_r": -0.2 if i % 3 == 0 else 0.4,
            "volume_confirmed": True,
            "strong_close": True,
        })
    report = build_breakout_report(rows, start_at=start, development_end_at=development_end)
    assert report["selected_on_train"] in {
        "raw_channel_breakout", "volume_confirmed_breakout", "strong_close_volume_breakout"
    }
    assert report["internal_holdout_result"]["n"] >= 50
    assert report["train_edge_pass"] is True
    assert report["internal_holdout_edge_pass"] is True
    assert report["strategy_family_edge_pass"] is True
    assert report["promotion_allowed"] is False
