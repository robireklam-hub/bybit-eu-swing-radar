from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from research.day_barrier_clear_partition_v1 import freeze_partition


def _events(count: int, *, clears: int | None = None, longs: int | None = None):
    start = datetime(2026, 8, 21, 16, 0, tzinfo=timezone.utc)
    clears = count // 2 if clears is None else clears
    longs = count // 2 if longs is None else longs
    rows = []
    for index in range(count):
        rows.append({
            "event_id": f"event-{index:03d}",
            "symbol": f"TEST{index % 7}USDC",
            "side": "long" if index < longs else "short",
            "terminal_status": "cleared" if index < clears else "invalidated_boundary",
            "resolved_at": (start + timedelta(minutes=5 * index)).isoformat(),
        })
    return rows


def test_59_events_do_not_freeze_partial_development_cohort():
    result = freeze_partition(_events(59, clears=30, longs=30))
    assert result["development_partition_ready"] is False
    assert result["development_event_ids"] == []
    assert result["development_partition_fingerprint"] is None
    assert result["development_boundary"] is None
    assert result["outcome_visible"] is False
    assert result["promotion_allowed"] is False


def test_first_60_terminal_events_freeze_development_without_opening_outcomes():
    rows = _events(60, clears=30, longs=30)
    result = freeze_partition(rows)
    assert result["development_partition_ready"] is True
    assert result["development_analysis_eligible"] is True
    assert len(result["development_event_ids"]) == 60
    assert result["development_partition_fingerprint"]
    assert result["partition_boundary_order"] == ["resolved_at", "event_id"]
    assert result["development_boundary"] == {
        "resolved_at": rows[-1]["resolved_at"],
        "event_id": rows[-1]["event_id"],
    }
    assert result["validation_partition_ready"] is False
    assert result["validation_boundary"] is None
    assert result["outcome_visible"] is False
    assert result["threshold_search_allowed"] is False
    assert result["execution_authorized"] is False


def test_next_40_are_untouched_validation_and_development_never_expands():
    base = _events(60, clears=30, longs=30)
    first = freeze_partition(base)
    later = _events(100, clears=50, longs=50)[60:]
    full = freeze_partition(base + later)
    assert full["development_event_ids"] == first["development_event_ids"]
    assert full["development_partition_fingerprint"] == first["development_partition_fingerprint"]
    assert full["development_boundary"] == first["development_boundary"]
    assert full["validation_partition_ready"] is True
    assert len(full["validation_event_ids"]) == 40
    assert full["validation_boundary"] is not None
    assert set(full["development_event_ids"]).isdisjoint(full["validation_event_ids"])


def test_input_order_does_not_change_frozen_partition():
    rows = _events(100, clears=50, longs=50)
    forward = freeze_partition(rows)
    reverse = freeze_partition(list(reversed(rows)))
    assert forward["development_partition_fingerprint"] == reverse["development_partition_fingerprint"]
    assert forward["validation_partition_fingerprint"] == reverse["validation_partition_fingerprint"]
    assert forward["development_boundary"] == reverse["development_boundary"]
    assert forward["validation_boundary"] == reverse["validation_boundary"]


def test_equivalent_timezone_representations_do_not_change_partition_identity():
    base = _events(100, clears=50, longs=50)
    equivalent = [dict(row) for row in base]
    for index, row in enumerate(equivalent):
        instant = datetime.fromisoformat(row["resolved_at"])
        if index % 3 == 0:
            row["resolved_at"] = instant.isoformat().replace("+00:00", "Z")
        elif index % 3 == 1:
            row["resolved_at"] = instant.astimezone(timezone(timedelta(hours=2))).isoformat()

    original = freeze_partition(base)
    normalized = freeze_partition(equivalent)

    assert normalized["development_event_ids"] == original["development_event_ids"]
    assert normalized["validation_event_ids"] == original["validation_event_ids"]
    assert normalized["development_partition_fingerprint"] == original["development_partition_fingerprint"]
    assert normalized["validation_partition_fingerprint"] == original["validation_partition_fingerprint"]
    assert normalized["development_boundary"] == original["development_boundary"]
    assert normalized["validation_boundary"] == original["validation_boundary"]


def test_composite_boundary_uses_event_id_when_resolved_at_ties():
    rows = _events(61, clears=31, longs=31)
    tied_time = rows[59]["resolved_at"]
    rows[58]["resolved_at"] = tied_time
    rows[59]["resolved_at"] = tied_time
    rows[60]["resolved_at"] = tied_time
    rows[58]["event_id"] = "event-tie-a"
    rows[59]["event_id"] = "event-tie-b"
    rows[60]["event_id"] = "event-tie-c"

    result = freeze_partition(rows)

    assert result["development_partition_ready"] is True
    assert result["development_boundary"] == {
        "resolved_at": tied_time,
        "event_id": "event-tie-b",
    }
    assert result["development_event_ids"][-1] == "event-tie-b"
    assert "event-tie-c" not in result["development_event_ids"]


def test_fixed_60_does_not_extend_when_group_balance_fails():
    rows = _events(80, clears=10, longs=30)
    result = freeze_partition(rows)
    assert result["development_partition_ready"] is True
    assert len(result["development_event_ids"]) == 60
    assert result["development_analysis_eligible"] is False
    assert result["development_balance"]["cleared"] == 10
    assert result["reasons"] == ["fixed_development_cohort_failed_preregistered_group_balance"]


def test_outcome_bearing_input_fails_closed():
    rows = _events(60, clears=30, longs=30)
    rows[0]["context"] = {"nested": {"net_r": 1.2}}
    with pytest.raises(ValueError, match="outcome-bearing"):
        freeze_partition(rows)


def test_duplicate_event_identity_fails_closed():
    rows = _events(60, clears=30, longs=30)
    rows[-1]["event_id"] = rows[0]["event_id"]
    with pytest.raises(ValueError, match="duplicate event_id"):
        freeze_partition(rows)


def test_nonterminal_event_cannot_enter_partition():
    rows = _events(60, clears=30, longs=30)
    rows[0]["terminal_status"] = "pending"
    with pytest.raises(ValueError, match="only terminal"):
        freeze_partition(rows)
