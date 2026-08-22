from datetime import datetime, timedelta, timezone

import pytest

from research.day_barrier_clear_followup_v2 import (
    DEVELOPMENT_PER_SIDE,
    VALIDATION_PER_SIDE,
    freeze_balanced_followup,
)


BASE = datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc)


def _event(index: int, side: str, status: str = "cleared") -> dict[str, str]:
    return {
        "event_id": f"event-{index:03d}-{side}",
        "symbol": "BTCUSDC" if side == "long" else "ETHUSDC",
        "side": side,
        "terminal_status": status,
        "resolved_at": (BASE + timedelta(minutes=index)).isoformat(),
    }


def _start() -> dict[str, str]:
    return {"resolved_at": BASE.isoformat(), "event_id": "boundary"}


def test_requires_explicit_fresh_start_boundary():
    with pytest.raises(ValueError, match="start_boundary"):
        freeze_balanced_followup([], start_boundary=None)


def test_excludes_every_event_at_or_before_composite_boundary():
    before = _event(-1, "long")
    at_time_lower_id = {
        **_event(0, "long"),
        "event_id": "aaa",
    }
    after_same_time = {
        **_event(0, "long"),
        "event_id": "zzz",
    }
    result = freeze_balanced_followup(
        [before, at_time_lower_id, after_same_time],
        start_boundary=_start(),
    )
    assert result["eligible_post_boundary_event_count"] == 1
    assert result["eligible_long_count"] == 1


def test_freezes_earliest_30_per_side_not_natural_frequency_first_60():
    events = []
    for index in range(1, 57):
        events.append(_event(index, "long", "cleared" if index % 2 else "invalidated_boundary"))
    for offset in range(1, 31):
        index = 100 + offset
        events.append(_event(index, "short", "cleared" if offset % 2 else "invalidated_boundary"))

    result = freeze_balanced_followup(list(reversed(events)), start_boundary=_start())

    assert result["development_partition_ready"] is True
    assert result["development_analysis_eligible"] is True
    assert len(result["development_event_ids"]) == 2 * DEVELOPMENT_PER_SIDE
    assert result["development_balance"]["long"] == DEVELOPMENT_PER_SIDE
    assert result["development_balance"]["short"] == DEVELOPMENT_PER_SIDE
    assert result["development_balance"]["cleared"] == 30
    assert result["development_balance"]["noncleared"] == 30
    assert result["outcome_visible"] is False
    assert result["threshold_search_allowed"] is False
    assert result["promotion_allowed"] is False
    assert result["live_strategy_mutation"] is False
    assert result["execution_authorized"] is False


def test_validation_is_next_20_per_side_and_disjoint():
    events = []
    total_per_side = DEVELOPMENT_PER_SIDE + VALIDATION_PER_SIDE
    for offset in range(1, total_per_side + 1):
        events.append(_event(offset, "long", "cleared" if offset % 2 else "invalidated_boundary"))
        events.append(_event(100 + offset, "short", "cleared" if offset % 2 else "invalidated_boundary"))

    result = freeze_balanced_followup(events, start_boundary=_start())
    development = set(result["development_event_ids"])
    validation = set(result["validation_event_ids"])

    assert result["validation_partition_ready"] is True
    assert len(validation) == 2 * VALIDATION_PER_SIDE
    assert development.isdisjoint(validation)
    assert result["validation_outcome_visible"] is False


def test_incomplete_short_quota_never_pads_development_with_longs():
    events = [_event(index, "long") for index in range(1, 81)]
    events += [_event(100 + index, "short") for index in range(1, DEVELOPMENT_PER_SIDE)]

    result = freeze_balanced_followup(events, start_boundary=_start())

    assert result["development_partition_ready"] is False
    assert result["development_event_ids"] == []
    assert result["development_partition_fingerprint"] is None
    assert result["reasons"] == [
        "insufficient_per_side_terminal_events_for_fixed_development_cohort"
    ]


def test_outcome_bearing_fields_are_rejected_before_freeze():
    event = _event(1, "long")
    event["mfe"] = "1.5R"
    with pytest.raises(ValueError, match="outcome-bearing"):
        freeze_balanced_followup([event], start_boundary=_start())


def test_fixed_development_terminal_state_balance_cannot_be_repaired_by_extra_events():
    events = []
    for offset in range(1, DEVELOPMENT_PER_SIDE + 1):
        events.append(_event(offset, "long", "cleared"))
        events.append(_event(100 + offset, "short", "cleared"))
    # Later non-clear events are intentionally too late to alter the frozen 30+30 DEVELOPMENT cohort.
    for offset in range(1, 31):
        events.append(_event(200 + offset, "long", "invalidated_boundary"))
        events.append(_event(300 + offset, "short", "invalidated_boundary"))

    result = freeze_balanced_followup(events, start_boundary=_start())

    assert result["development_partition_ready"] is True
    assert result["development_analysis_eligible"] is False
    assert result["development_balance"]["cleared"] == 60
    assert result["development_balance"]["noncleared"] == 0
    assert result["reasons"] == [
        "fixed_development_cohort_failed_preregistered_terminal_state_balance"
    ]
