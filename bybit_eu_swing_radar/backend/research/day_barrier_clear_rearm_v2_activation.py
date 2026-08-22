"""Frozen prospective activation contract for barrier-clear v2.

Research only. This module activates collection eligibility after a fixed UTC
boundary; it does not expose outcomes, change live strategy semantics, authorize
execution, or permit historical backfill.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from research.day_barrier_clear_rearm_v2 import TRIAL_ID

PREREGISTRATION_MERGE_SHA = "24a40b3251f7737757f44020fea5cdd062f0fa3e"
PREREGISTRATION_MERGED_AT = "2026-08-22T11:51:43+00:00"
ACTIVATION_BOUNDARY = "2026-08-22T13:15:00+00:00"
ACTIVATION_RULE = "TERMINAL_EVENTS_RESOLVED_STRICTLY_AFTER_FROZEN_UTC_BOUNDARY"


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("activation timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def activation_status() -> dict[str, Any]:
    prereg = _utc(PREREGISTRATION_MERGED_AT)
    boundary = _utc(ACTIVATION_BOUNDARY)
    if boundary <= prereg:
        raise RuntimeError("v2 activation boundary must be strictly after preregistration merge")
    return {
        "trial_id": TRIAL_ID,
        "status": "ACTIVATED_PROSPECTIVE_ONLY",
        "preregistration_merge_sha": PREREGISTRATION_MERGE_SHA,
        "preregistration_merged_at": prereg.isoformat(),
        "activation_boundary": boundary.isoformat(),
        "activation_rule": ACTIVATION_RULE,
        "historical_backfill_allowed": False,
        "v1_event_reuse_allowed": False,
        "outcome_visible": False,
        "threshold_search_allowed": False,
        "promotion_allowed": False,
        "execution_authorized": False,
        "live_strategy_mutated": False,
    }
