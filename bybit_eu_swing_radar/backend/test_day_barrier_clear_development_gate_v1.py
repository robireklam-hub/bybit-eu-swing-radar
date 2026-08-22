from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from research.day_barrier_clear_development_gate_v1 import (
    GATE_SPEC_VERSION,
    evaluate_development_gate,
)


def _events(count: int, *, clears: int, longs: int):
    start = datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        rows.append({
            "event_id": f"evt-{index:03d}",
            "symbol": "BTCUSDC" if index % 2 == 0 else "ETHUSDC",
            "side": "long" if index < longs else "short",
            "terminal_status": "cleared" if index < clears else "invalidated_boundary",
            "resolved_at": (start + timedelta(minutes=5 * index)).isoformat(),
        })
    return rows


def test_gate_opens_development_outcome_request_only_after_fixed_balanced_60():
    result = evaluate_development_gate(_events(60, clears=30, longs=30))

    assert result["gate_spec_version"] == GATE_SPEC_VERSION
    assert result["research_only"] is True
    assert result["label_blind_gate"] is True
    assert result["outcome_fields_read"] is False
    assert result["development_partition_ready"] is True
    assert result["development_balance_ready"] is True
    assert len(result["development_event_ids"]) == 60
    assert result["development_partition_fingerprint"]
    assert result["development_boundary"] == {
        "resolved_at": "2026-08-21T04:55:00+00:00",
        "event_id": "evt-059",
    }
    assert result["development_outcome_opening_authorized"] is True
    assert result["validation_outcome_opening_authorized"] is False
    assert result["threshold_search_allowed"] is False
    assert result["promotion_allowed"] is False
    assert result["live_strategy_mutation"] is False
    assert result["execution_authorized"] is False
    assert result["reasons"] == []


def test_gate_stays_closed_before_60_terminal_events():
    result = evaluate_development_gate(_events(59, clears=30, longs=30))

    assert result["development_partition_ready"] is False
    assert result["development_balance_ready"] is False
    assert result["development_event_ids"] == []
    assert result["development_partition_fingerprint"] is None
    assert result["development_boundary"] is None
    assert result["development_outcome_opening_authorized"] is False
    assert "insufficient_terminal_events_for_fixed_development_cohort" in result["reasons"]


def test_gate_does_not_extend_failed_first_60_with_later_favorable_events():
    rows = _events(60, clears=10, longs=30)
    rows.extend(_events(20, clears=20, longs=20))
    for index, row in enumerate(rows[60:], start=60):
        row["event_id"] = f"evt-{index:03d}"
        row["resolved_at"] = (
            datetime(2026, 8, 21, 5, 0, tzinfo=timezone.utc)
            + timedelta(minutes=5 * (index - 60))
        ).isoformat()

    result = evaluate_development_gate(rows)

    assert result["terminal_event_count"] == 80
    assert result["development_partition_ready"] is True
    assert result["development_balance_ready"] is False
    assert result["development_outcome_opening_authorized"] is False
    assert len(result["development_event_ids"]) == 60
    assert result["development_event_ids"][-1] == "evt-059"
    assert "fixed_development_cohort_failed_preregistered_group_balance" in result["reasons"]


def test_gate_rejects_outcome_bearing_input_before_opening():
    rows = _events(60, clears=30, longs=30)
    rows[0]["pnl"] = 1.25

    with pytest.raises(ValueError, match="outcome-bearing field"):
        evaluate_development_gate(rows)


def test_gate_never_opens_validation_even_when_100_terminal_events_exist():
    rows = _events(100, clears=50, longs=50)
    result = evaluate_development_gate(rows)

    assert result["development_outcome_opening_authorized"] is True
    assert result["validation_outcome_opening_authorized"] is False
    assert result["threshold_search_allowed"] is False
    assert result["promotion_allowed"] is False
    assert result["execution_authorized"] is False
