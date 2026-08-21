from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from research.swing_liquidity_cohort_partition import (
    DEVELOPMENT_TARGET_MATURED_EVENTS,
    build_label_blind_cohort_partition,
)


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def event(index: int, *, trigger_close: datetime | None = None) -> dict:
    close = trigger_close or (BASE + timedelta(hours=4 * index))
    symbol = f"S{index:02d}USDC"
    side = "long" if index % 2 == 0 else "short"
    return {
        "event_id": f"{symbol}:{side}:{close.isoformat()}",
        "symbol": symbol,
        "side": side,
        "trigger_close_at": close.isoformat(),
        "matures_at": (close + timedelta(days=10)).isoformat(),
        "label_blind": True,
        "outcome_visible": False,
        "promotion_allowed": False,
    }


def test_partition_stays_closed_before_sixty_matured_events():
    rows = [event(i) for i in range(59)]
    checked_at = BASE + timedelta(days=30)
    result = build_label_blind_cohort_partition(rows, checked_at=checked_at)

    assert result["matured_event_count"] == 59
    assert result["development_partition_ready"] is False
    assert result["development_event_ids"] == []
    assert result["validation_event_ids"] == []
    assert result["partition_fingerprint"] is None
    assert result["outcome_visible"] is False
    assert result["threshold_search_allowed"] is False
    assert result["promotion_allowed"] is False


def test_first_sixty_matured_events_freeze_development_and_later_events_validation():
    rows = [event(i) for i in range(64)]
    # Shuffle input so ordering cannot depend on API/list order.
    rows = list(reversed(rows))
    checked_at = BASE + timedelta(days=30)
    result = build_label_blind_cohort_partition(rows, checked_at=checked_at)

    assert result["development_partition_ready"] is True
    assert result["development_event_count"] == DEVELOPMENT_TARGET_MATURED_EVENTS
    assert len(result["development_event_ids"]) == 60
    assert len(result["validation_event_ids"]) == 4
    assert result["development_event_ids"][0].startswith("S00USDC:long:")
    assert result["development_event_ids"][-1].startswith("S59USDC:short:")
    assert result["validation_event_ids"][0].startswith("S60USDC:long:")
    assert isinstance(result["partition_fingerprint"], str)
    assert len(result["partition_fingerprint"]) == 64


def test_partition_fingerprint_is_stable_under_input_reordering():
    rows = [event(i) for i in range(61)]
    checked_at = BASE + timedelta(days=30)
    forward = build_label_blind_cohort_partition(rows, checked_at=checked_at)
    reverse = build_label_blind_cohort_partition(reversed(rows), checked_at=checked_at)
    assert forward["partition_fingerprint"] == reverse["partition_fingerprint"]
    assert forward["development_event_ids"] == reverse["development_event_ids"]


def test_pending_events_after_frozen_boundary_are_validation_without_outcome_access():
    rows = [event(i) for i in range(60)]
    late = event(60, trigger_close=BASE + timedelta(days=25))
    rows.append(late)
    checked_at = BASE + timedelta(days=20)
    result = build_label_blind_cohort_partition(rows, checked_at=checked_at)

    assert result["matured_event_count"] == 60
    assert result["development_partition_ready"] is True
    assert result["validation_event_ids"] == [late["event_id"]]
    assert result["outcome_visible"] is False


def test_outcome_bearing_event_fails_closed():
    rows = [event(i) for i in range(60)]
    rows[3]["net_r"] = 1.25
    with pytest.raises(ValueError, match="contains_outcome_fields:net_r"):
        build_label_blind_cohort_partition(rows, checked_at=BASE + timedelta(days=30))


def test_duplicate_event_identity_fails_closed():
    rows = [event(i) for i in range(60)]
    rows.append(dict(rows[10]))
    with pytest.raises(ValueError, match="duplicate_event_id"):
        build_label_blind_cohort_partition(rows, checked_at=BASE + timedelta(days=30))
