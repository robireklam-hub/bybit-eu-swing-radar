from datetime import datetime

import pytest

from research.day_barrier_clear_rearm_v2 import build_side_stratified_partition
from research.day_barrier_clear_rearm_v2_activation import (
    ACTIVATION_BOUNDARY,
    PREREGISTRATION_MERGED_AT,
    activation_status,
)


def test_v2_activation_is_strictly_post_preregistration_and_preserves_firewalls():
    status = activation_status()
    assert status["status"] == "ACTIVATED_PROSPECTIVE_ONLY"
    assert datetime.fromisoformat(ACTIVATION_BOUNDARY) > datetime.fromisoformat(PREREGISTRATION_MERGED_AT)
    assert status["historical_backfill_allowed"] is False
    assert status["v1_event_reuse_allowed"] is False
    assert status["outcome_visible"] is False
    assert status["threshold_search_allowed"] is False
    assert status["promotion_allowed"] is False
    assert status["execution_authorized"] is False
    assert status["live_strategy_mutated"] is False


def test_v2_partition_rejects_event_at_activation_boundary():
    with pytest.raises(ValueError, match="strictly after"):
        build_side_stratified_partition(
            [
                {
                    "event_id": "boundary",
                    "side": "long",
                    "terminal": True,
                    "resolved_at": ACTIVATION_BOUNDARY,
                }
            ],
            activation_boundary=ACTIVATION_BOUNDARY,
        )


def test_v2_partition_accepts_first_terminal_event_strictly_after_boundary_without_opening_outcomes():
    boundary = datetime.fromisoformat(ACTIVATION_BOUNDARY)
    event_time = boundary.replace(second=1)
    result = build_side_stratified_partition(
        [
            {
                "event_id": "first-post-activation",
                "side": "short",
                "terminal": True,
                "resolved_at": event_time.isoformat(),
            }
        ],
        activation_boundary=ACTIVATION_BOUNDARY,
    )
    assert result["activated"] is True
    assert result["development_ready"] is False
    assert result["development_event_count"] == 0
    assert result["validation_ready"] is False
    assert result["outcome_visible"] is False
    assert result["threshold_search_allowed"] is False
    assert result["promotion_allowed"] is False
    assert result["execution_authorized"] is False
