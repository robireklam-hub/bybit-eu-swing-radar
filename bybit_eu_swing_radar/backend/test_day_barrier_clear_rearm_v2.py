from datetime import datetime, timedelta, timezone

import pytest

from research.day_barrier_clear_rearm_v2 import (
    DEVELOPMENT_PER_SIDE,
    VALIDATION_PER_SIDE,
    build_side_stratified_partition,
    preregistration_status,
)


def _event(event_id: str, side: str, resolved_at: datetime, *, captured_at: datetime | None = None, **extra):
    if captured_at is None:
        captured_at = resolved_at - timedelta(seconds=30)
    row = {
        "event_id": event_id,
        "side": side,
        "terminal": True,
        "captured_at": captured_at.isoformat(),
        "resolved_at": resolved_at.isoformat(),
    }
    row.update(extra)
    return row


def test_v2_is_preregistered_but_not_activated_and_preserves_firewalls():
    status = preregistration_status()
    assert status["status"] == "PREREGISTERED_NOT_ACTIVATED"
    assert status["activation_required"] is True
    assert status["historical_backfill_allowed"] is False
    assert status["v1_event_reuse_allowed"] is False
    assert status["development_target"] == 60
    assert status["development_per_side"] == 30
    assert status["validation_target"] == 40
    assert status["validation_per_side"] == 20
    assert status["outcome_visible"] is False
    assert status["threshold_search_allowed"] is False
    assert status["promotion_allowed"] is False
    assert status["execution_authorized"] is False
    assert status["live_strategy_mutated"] is False


def test_development_does_not_freeze_until_both_side_quotas_are_met():
    boundary = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    events = [
        _event(f"L{i:02d}", "long", boundary + timedelta(minutes=i + 1))
        for i in range(DEVELOPMENT_PER_SIDE + 20)
    ]
    events += [
        _event(f"S{i:02d}", "short", boundary + timedelta(minutes=100 + i))
        for i in range(DEVELOPMENT_PER_SIDE - 1)
    ]
    result = build_side_stratified_partition(events, activation_boundary=boundary)
    assert result["development_ready"] is False
    assert result["development_event_count"] == 0
    assert result["development_fingerprint"] is None


def test_exact_30_30_development_and_20_20_validation_are_frozen_deterministically():
    boundary = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    events = []
    for i in range(DEVELOPMENT_PER_SIDE + VALIDATION_PER_SIDE + 5):
        events.append(_event(f"L{i:02d}", "long", boundary + timedelta(minutes=i + 1)))
        events.append(_event(f"S{i:02d}", "short", boundary + timedelta(minutes=i + 1, seconds=30)))

    first = build_side_stratified_partition(events, activation_boundary=boundary)
    second = build_side_stratified_partition(list(reversed(events)), activation_boundary=boundary.isoformat())

    assert first["development_ready"] is True
    assert first["development_event_count"] == 60
    assert first["development_long_count"] == 30
    assert first["development_short_count"] == 30
    assert first["validation_ready"] is True
    assert first["validation_event_count"] == 40
    assert first["validation_long_count"] == 20
    assert first["validation_short_count"] == 20
    assert first["development_fingerprint"] == second["development_fingerprint"]
    assert first["validation_fingerprint"] == second["validation_fingerprint"]
    assert first["development_events"] == second["development_events"]
    assert first["validation_events"] == second["validation_events"]


def test_events_at_or_before_activation_boundary_are_rejected_to_prevent_backfill():
    boundary = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="resolve strictly after"):
        build_side_stratified_partition(
            [_event("old", "long", boundary, captured_at=boundary + timedelta(seconds=1))],
            activation_boundary=boundary,
        )


def test_pre_activation_parent_is_rejected_even_if_resolution_is_post_activation():
    boundary = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="parents must be captured strictly after"):
        build_side_stratified_partition(
            [
                _event(
                    "old-parent",
                    "short",
                    boundary + timedelta(minutes=10),
                    captured_at=boundary - timedelta(minutes=1),
                )
            ],
            activation_boundary=boundary,
        )


def test_resolution_cannot_precede_parent_capture():
    boundary = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="resolved_at must not precede captured_at"):
        build_side_stratified_partition(
            [
                _event(
                    "time-reversed",
                    "long",
                    boundary + timedelta(minutes=5),
                    captured_at=boundary + timedelta(minutes=6),
                )
            ],
            activation_boundary=boundary,
        )


def test_outcome_bearing_input_fails_closed_before_partitioning():
    boundary = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="outcome-bearing"):
        build_side_stratified_partition(
            [_event("L1", "long", boundary + timedelta(minutes=1), pnl=12.3)],
            activation_boundary=boundary,
        )


def test_duplicate_identity_and_nonterminal_rows_fail_closed():
    boundary = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    duplicate = [
        _event("same", "long", boundary + timedelta(minutes=1)),
        _event("same", "short", boundary + timedelta(minutes=2)),
    ]
    with pytest.raises(ValueError, match="duplicate event_id"):
        build_side_stratified_partition(duplicate, activation_boundary=boundary)

    nonterminal = _event("pending", "long", boundary + timedelta(minutes=1))
    nonterminal["terminal"] = False
    with pytest.raises(ValueError, match="only terminal events"):
        build_side_stratified_partition([nonterminal], activation_boundary=boundary)
