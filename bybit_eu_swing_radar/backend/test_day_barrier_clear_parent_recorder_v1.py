from datetime import datetime, timedelta, timezone

from research.day_barrier_clear_parent_recorder_v1 import build_parent_record


def _candidate(**overrides):
    item = {
        "strategy_version": "0.7.5",
        "symbol": "BTCUSDC",
        "side": "long",
        "category": "WATCH_ONLY",
        "decision": "NO_TRADE",
        "tradeable": True,
        "shortable": True,
        "execution_status": "DAY_TRADE_EXECUTABLE",
        "setup_score": 71.88,
        "expansion_score": 62.05,
        "side_direction_score": 57.6,
        "quality_score": 99.99,
        "structure_15m": "bullish",
        "structure_1h": "bullish",
        "trigger": {
            "triggered": True,
            "route": "CLOSED_5M_RANGE_BREAKOUT",
            "price": 69863.5,
            "event_bar_time": "2026-08-21T15:05:00+00:00",
            "boundary_held": True,
        },
        "metrics": {
            "nearest_structural_barrier": 69998.4,
            "barrier_before_tp2": True,
            "target_path_valid": False,
            "expected_rr_without_barrier": 1.8,
            "spread_bps": 2.1,
            "volume_ratio_5m": 1.7,
            "volume_ratio_15m": 1.3,
        },
        "derivatives": {"oi": 123.0, "funding": 0.001},
    }
    item.update(overrides)
    return item


def test_parent_record_is_v075_label_blind_and_does_not_store_stale_geometry():
    captured = datetime(2026, 8, 21, 15, 7, tzinfo=timezone.utc)
    row = build_parent_record(
        _candidate(
            entry_zone={"low": 1, "high": 2},
            stop=0.5,
            targets=[3, 4, 5],
            derivatives={"oi": 123, "pnl": 999, "nested": {"mfe": 2, "funding": 0.001}},
        ),
        captured_at=captured,
        prospective_start_at=captured - timedelta(minutes=5),
        source_commit_sha="abc123",
    )
    assert row is not None
    assert row["parent_strategy_version"] == "0.7.5"
    assert row["symbol"] == "BTCUSDC"
    assert row["frozen_barrier_price"] == 69998.4
    assert row["research_only"] is True
    assert row["execution_authorized"] is False
    payload = row["snapshot_payload"]
    text = str(payload).lower()
    assert "entry_zone" not in text
    assert "targets" not in text
    assert "'stop'" not in text
    assert "'pnl'" not in text
    assert "'mfe'" not in text
    assert payload["context"]["derivatives"]["nested"] == {"funding": 0.001}
    assert payload["fresh_geometry_required_after_clear"] is True


def test_prospective_boundary_blocks_pre_start_parent_backfill():
    captured = datetime(2026, 8, 21, 15, 20, tzinfo=timezone.utc)
    row = build_parent_record(
        _candidate(),
        captured_at=captured,
        prospective_start_at=datetime(2026, 8, 21, 15, 10, tzinfo=timezone.utc),
        source_commit_sha=None,
    )
    assert row is None


def test_event_key_is_deterministic_for_same_frozen_parent():
    captured = datetime(2026, 8, 21, 15, 7, tzinfo=timezone.utc)
    kwargs = dict(
        captured_at=captured,
        prospective_start_at=captured - timedelta(minutes=5),
        source_commit_sha="sha",
    )
    first = build_parent_record(_candidate(), **kwargs)
    second = build_parent_record(_candidate(derivatives={}), **kwargs)
    assert first is not None and second is not None
    assert first["event_key"] == second["event_key"]


def test_missing_derivatives_do_not_gate_parent_but_unborrowable_short_does():
    captured = datetime(2026, 8, 21, 15, 7, tzinfo=timezone.utc)
    kwargs = dict(
        captured_at=captured,
        prospective_start_at=captured - timedelta(minutes=5),
        source_commit_sha="sha",
    )
    assert build_parent_record(_candidate(derivatives={}), **kwargs) is not None

    short = _candidate(side="short", shortable=False)
    short["metrics"]["nearest_structural_barrier"] = 69252.4
    assert build_parent_record(short, **kwargs) is None


def test_wrong_strategy_or_non_usdc_parent_is_rejected():
    captured = datetime(2026, 8, 21, 15, 7, tzinfo=timezone.utc)
    kwargs = dict(
        captured_at=captured,
        prospective_start_at=captured - timedelta(minutes=5),
        source_commit_sha="sha",
    )
    assert build_parent_record(_candidate(strategy_version="0.7.6"), **kwargs) is None
    assert build_parent_record(_candidate(symbol="BTCUSDT"), **kwargs) is None
