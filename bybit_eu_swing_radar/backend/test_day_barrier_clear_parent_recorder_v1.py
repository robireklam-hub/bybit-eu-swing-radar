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


def _captured():
    return datetime(2026, 8, 21, 15, 12, tzinfo=timezone.utc)


def test_parent_record_is_v075_label_blind_and_does_not_store_stale_geometry():
    captured = _captured()
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
    assert row["trigger_boundary"] == 69863.5
    assert row["boundary_kind"] == "RANGE_BREAKOUT_BOUNDARY"
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


def test_prospective_boundary_uses_bar_close_not_bar_start():
    captured = _captured()
    # Event bar starts 15:05 and closes 15:10. It was not knowable at 15:09:59,
    # so a recorder initialized just before close may admit it after close.
    admitted = build_parent_record(
        _candidate(),
        captured_at=captured,
        prospective_start_at=datetime(2026, 8, 21, 15, 9, 59, tzinfo=timezone.utc),
        source_commit_sha=None,
    )
    assert admitted is not None

    blocked = build_parent_record(
        _candidate(),
        captured_at=captured,
        prospective_start_at=datetime(2026, 8, 21, 15, 10, 1, tzinfo=timezone.utc),
        source_commit_sha=None,
    )
    assert blocked is None


def test_sweep_parent_freezes_reclaim_level_not_candidate_entry_as_boundary():
    captured = _captured()
    candidate = _candidate()
    candidate["trigger"] = {
        "triggered": True,
        "route": "LIQUIDITY_SWEEP_RECLAIM",
        "price": 69920.0,
        "event_bar_time": None,
        "sweep_confirmation": {
            "sweep_level": 69850.0,
            "sweep_time": "2026-08-21T15:00:00+00:00",
            "reclaim_time": "2026-08-21T15:00:00+00:00",
            "structure_shift_time_5m": "2026-08-21T15:05:00+00:00",
            "candidate_entry": 69920.0,
        },
    }
    row = build_parent_record(
        candidate,
        captured_at=captured,
        prospective_start_at=captured - timedelta(minutes=5),
        source_commit_sha="sha",
    )
    assert row is not None
    assert row["trigger_price"] == 69920.0
    assert row["trigger_boundary"] == 69850.0
    assert row["boundary_kind"] == "SWEEP_RECLAIM_LEVEL"
    assert row["parent_event_time"] == datetime(2026, 8, 21, 15, 5, tzinfo=timezone.utc)


def test_event_key_is_deterministic_for_same_frozen_parent():
    captured = _captured()
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
    captured = _captured()
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
    captured = _captured()
    kwargs = dict(
        captured_at=captured,
        prospective_start_at=captured - timedelta(minutes=5),
        source_commit_sha="sha",
    )
    assert build_parent_record(_candidate(strategy_version="0.7.6"), **kwargs) is None
    assert build_parent_record(_candidate(symbol="BTCUSDT"), **kwargs) is None
