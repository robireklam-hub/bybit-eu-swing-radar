from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import day_worker as live
import research.day_barrier_clear_recorder_v1 as recorder
from worker import Bar, Instrument


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
        "setup_score": 72.0,
        "expansion_score": 62.0,
        "side_direction_score": 58.0,
        "quality_score": 90.0,
        "entry_zone": {"low": 100.0, "high": 100.1},
        "stop": 99.0,
        "targets": [101.0, 102.0, 103.0],
        "trigger": {
            "triggered": True,
            "route": "CLOSED_5M_RANGE_BREAKOUT",
            "price": 100.0,
            "event_bar_time": "2026-08-21T12:00:00+00:00",
            "sweep_confirmation": None,
        },
        "metrics": {
            "nearest_structural_barrier": 101.0,
            "barrier_before_tp2": True,
            "target_path_valid": False,
            "expected_rr_without_barrier": 1.8,
        },
        "derivatives": {},
        "research_context": {"session": "US", "mfe": 4.2},
    }
    item.update(overrides)
    return item


def _bar(start: datetime, close: float, *, span: float = 0.35) -> Bar:
    return Bar(
        start_ms=int(start.timestamp() * 1000),
        open=close - 0.05,
        high=close + span,
        low=close - span,
        close=close,
        volume=1000.0,
        turnover=100000.0,
    )


def _analysis() -> SimpleNamespace:
    base = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
    closes = [100.15 + ((index % 4) - 1.5) * 0.04 for index in range(30)]
    closes[20] = 100.45
    closes[21] = 101.20
    bars_5m = [_bar(base + timedelta(minutes=5 * index), close) for index, close in enumerate(closes)]

    base_15m = base - timedelta(hours=8)
    bars_15m = [
        _bar(base_15m + timedelta(minutes=15 * index), 96.0 + index * 0.03, span=0.45)
        for index in range(40)
    ]
    instrument = Instrument(
        symbol="BTCUSDC",
        base="BTC",
        quote="USDC",
        margin_trading="both",
        tick_size=0.1,
        turnover_24h=10_000_000.0,
        volume_24h=1000.0,
        last_price=101.2,
        bid=101.1,
        ask=101.2,
        spread_bps=10.0,
        price_change_24h_pct=1.0,
        tradeable=True,
        liquidity_reasons=[],
        discovery_source="mandatory",
    )
    return SimpleNamespace(
        instrument=instrument,
        bars_5m=bars_5m,
        bars_15m=bars_15m,
        shortable=True,
    )


def test_parent_row_is_prospective_v075_only_and_strips_outcome_keys_recursively():
    captured = datetime(2026, 8, 21, 12, 7, tzinfo=timezone.utc)
    row = recorder.parent_row_from_candidate(
        _candidate(),
        captured_at=captured,
        prospective_start_at=datetime(2026, 8, 21, 12, 4, tzinfo=timezone.utc),
        source_commit_sha="a" * 40,
    )
    assert row is not None
    assert row["parent_strategy_version"] == "0.7.5"
    assert row["trigger_boundary"] == 100.0
    assert row["boundary_kind"] == "RANGE_BREAKOUT_BOUNDARY"
    assert row["parent_payload"]["research_context"] == {"session": "US"}
    assert row["parent_payload"]["research_contract"]["execution_authorized"] is False

    old = recorder.parent_row_from_candidate(
        _candidate(),
        captured_at=captured,
        prospective_start_at=datetime(2026, 8, 21, 12, 6, tzinfo=timezone.utc),
        source_commit_sha="a" * 40,
    )
    assert old is None
    assert recorder.parent_row_from_candidate(
        _candidate(strategy_version="0.7.6"),
        captured_at=captured,
        prospective_start_at=datetime(2026, 8, 21, 12, 4, tzinfo=timezone.utc),
        source_commit_sha="a" * 40,
    ) is None


def test_sweep_parent_uses_reclaim_level_not_candidate_entry_as_boundary():
    candidate = _candidate()
    candidate["trigger"] = {
        "triggered": True,
        "route": "LIQUIDITY_SWEEP_RECLAIM",
        "price": 100.8,
        "event_bar_time": None,
        "sweep_confirmation": {
            "sweep_level": 99.7,
            "candidate_entry": 100.8,
            "structure_shift_time_5m": "2026-08-21T12:00:00+00:00",
        },
    }
    row = recorder.parent_row_from_candidate(
        candidate,
        captured_at=datetime(2026, 8, 21, 12, 7, tzinfo=timezone.utc),
        prospective_start_at=datetime(2026, 8, 21, 12, 4, tzinfo=timezone.utc),
        source_commit_sha="a" * 40,
    )
    assert row is not None
    assert row["trigger_boundary"] == 99.7
    assert row["boundary_kind"] == "SWEEP_RECLAIM_LEVEL"


def test_only_later_closed_bars_can_clear_and_fresh_geometry_uses_clear_close():
    analysis = _analysis()
    first_seen = datetime.fromtimestamp(
        (analysis.bars_5m[19].start_ms + recorder.FIVE_MIN_MS) / 1000,
        tz=timezone.utc,
    ) + timedelta(seconds=30)
    parent = {
        "parent_id": "p1",
        "first_seen_at": first_seen,
        "symbol": "BTCUSDC",
        "side": "long",
        "trigger_boundary": 100.0,
        "barrier_price": 101.0,
    }
    result = recorder.resolve_parent_against_analysis(parent, analysis)
    assert result is not None
    assert result["status"] == "CLEARED"
    assert result["bars_to_resolution"] == 2
    assert result["clear_close"] == 101.2
    geometry = result["fresh_geometry"]
    assert geometry["reference_entry"] == 101.2
    assert geometry["inherited_parent_geometry"] is None
    assert geometry["research_only"] is True
    assert geometry["execution_authorized"] is False
    assert "mfe" not in str(geometry).lower()
    assert "pnl" not in str(geometry).lower()


def test_boundary_loss_is_terminal_before_a_later_barrier_clear():
    analysis = _analysis()
    analysis.bars_5m[20] = _bar(
        datetime.fromtimestamp(analysis.bars_5m[20].start_ms / 1000, tz=timezone.utc),
        99.9,
    )
    first_seen = datetime.fromtimestamp(
        (analysis.bars_5m[19].start_ms + recorder.FIVE_MIN_MS) / 1000,
        tz=timezone.utc,
    ) + timedelta(seconds=30)
    result = recorder.resolve_parent_against_analysis(
        {
            "parent_id": "p2",
            "first_seen_at": first_seen,
            "symbol": "BTCUSDC",
            "side": "long",
            "trigger_boundary": 100.0,
            "barrier_price": 101.0,
        },
        analysis,
    )
    assert result is not None
    assert result["status"] == "INVALIDATED_BOUNDARY"
    assert result["bars_to_resolution"] == 1


def test_opposing_closed_15m_structure_invalidates_before_clear(monkeypatch):
    analysis = _analysis()
    first_seen = datetime.fromtimestamp(
        (analysis.bars_5m[19].start_ms + recorder.FIVE_MIN_MS) / 1000,
        tz=timezone.utc,
    ) + timedelta(seconds=30)
    monkeypatch.setattr(recorder, "classify_15m_structure", lambda *args, **kwargs: "BEARISH_SHIFT")
    result = recorder.resolve_parent_against_analysis(
        {
            "parent_id": "p3",
            "first_seen_at": first_seen,
            "symbol": "BTCUSDC",
            "side": "long",
            "trigger_boundary": 100.0,
            "barrier_price": 101.0,
        },
        analysis,
    )
    assert result is not None
    assert result["status"] == "INVALIDATED_STRUCTURE"
    assert result["structure_state_15m"] == "BEARISH_SHIFT"


def test_parent_id_is_deterministic_and_does_not_depend_on_barrier_geometry():
    event = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    first = recorder._parent_id(_candidate(), event)
    changed = _candidate()
    changed["metrics"]["nearest_structural_barrier"] = 101.5
    second = recorder._parent_id(changed, event)
    assert first == second
    assert len(first) == 64


def test_forbidden_outcomes_are_removed_at_any_nesting_depth():
    value = recorder._sanitize(
        {
            "safe": 1,
            "nested": {"mfe": 2, "safe2": [{"pnl": 3, "x": 4}]},
            "win": True,
        }
    )
    assert value == {"safe": 1, "nested": {"safe2": [{"x": 4}]}}


def test_recorder_is_explicitly_pinned_to_v075_not_live_default():
    assert recorder.PARENT_STRATEGY_VERSION == "0.7.5"
    assert live.DAY_STRATEGY_VERSION == "0.7.6"
    assert recorder.OUTCOME_VISIBILITY == "LOCKED_UNTIL_PREREGISTERED_DEVELOPMENT_GATE"
