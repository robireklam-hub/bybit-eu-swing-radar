from datetime import datetime, timedelta, timezone

from research.day_barrier_clear_rearm_v2_activation import ACTIVATION_BOUNDARY
from research.day_barrier_clear_rearm_v2_status import build_v2_status_from_rows


def _row(event_id: str, side: str, captured_at: datetime, resolved_at: datetime, status: str = "CLEARED"):
    return {
        "event_key": event_id,
        "side": side,
        "captured_at": captured_at,
        "resolved_at": resolved_at,
        "resolution_status": status,
    }


def test_v2_status_excludes_pre_activation_parents_even_if_they_resolve_later():
    boundary = datetime.fromisoformat(ACTIVATION_BOUNDARY)
    rows = [
        _row("old-parent", "short", boundary - timedelta(minutes=1), boundary + timedelta(minutes=10)),
        _row("new-parent", "long", boundary + timedelta(minutes=1), boundary + timedelta(minutes=11)),
    ]
    status = build_v2_status_from_rows(rows, source_commit_sha="a" * 40, captured_at=boundary + timedelta(hours=1))
    assert status["eligible_terminal_event_count"] == 1
    assert status["eligible_long_count"] == 1
    assert status["eligible_short_count"] == 0
    assert status["excluded_pre_activation_parent_count"] == 1
    assert status["pre_activation_parent_reuse_allowed"] is False
    assert status["outcome_fields_read"] is False


def test_v2_status_does_not_freeze_partial_side_stratified_development():
    boundary = datetime.fromisoformat(ACTIVATION_BOUNDARY)
    rows = []
    for i in range(40):
        rows.append(_row(f"L{i:02d}", "long", boundary + timedelta(seconds=i + 1), boundary + timedelta(minutes=i + 1)))
    for i in range(29):
        rows.append(_row(f"S{i:02d}", "short", boundary + timedelta(seconds=100 + i), boundary + timedelta(minutes=100 + i)))
    status = build_v2_status_from_rows(rows, source_commit_sha="b" * 40, captured_at=boundary + timedelta(hours=4))
    assert status["eligible_long_count"] == 40
    assert status["eligible_short_count"] == 29
    assert status["development_ready"] is False
    assert status["development_event_count"] == 0
    assert status["development_fingerprint"] is None
    assert status["validation_ready"] is False
    assert status["outcome_visible"] is False
    assert status["threshold_search_allowed"] is False
    assert status["promotion_allowed"] is False
    assert status["execution_authorized"] is False


def test_v2_status_freezes_exact_30_30_without_using_extra_side_events():
    boundary = datetime.fromisoformat(ACTIVATION_BOUNDARY)
    rows = []
    for i in range(35):
        rows.append(_row(f"L{i:02d}", "long", boundary + timedelta(seconds=i + 1), boundary + timedelta(minutes=i + 1)))
    for i in range(30):
        rows.append(_row(f"S{i:02d}", "short", boundary + timedelta(seconds=100 + i), boundary + timedelta(minutes=100 + i)))
    status = build_v2_status_from_rows(rows, source_commit_sha="c" * 40, captured_at=boundary + timedelta(hours=4))
    assert status["development_ready"] is True
    assert status["development_event_count"] == 60
    assert status["development_long_count"] == 30
    assert status["development_short_count"] == 30
    assert status["development_fingerprint"]
    assert status["validation_ready"] is False
