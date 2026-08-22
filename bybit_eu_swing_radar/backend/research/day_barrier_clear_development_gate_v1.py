"""Research-only DEVELOPMENT outcome-opening gate for day barrier-clear rearm.

This module verifies the preregistered fixed DEVELOPMENT partition before any
DEVELOPMENT outcomes may be requested. It never reads outcomes and never opens
VALIDATION, threshold search, promotion, live strategy mutation, or execution.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .day_barrier_clear_partition_v1 import (
    DEVELOPMENT_TARGET,
    PARTITION_SPEC_VERSION,
    STUDY_ID,
    freeze_partition,
)

GATE_SPEC_VERSION = "day-barrier-clear-development-gate-v1"


def evaluate_development_gate(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Verify the immutable first-60 DEVELOPMENT gate without reading outcomes."""
    partition = freeze_partition(events)

    reasons = list(partition.get("reasons") or [])
    development_ids = list(partition.get("development_event_ids") or [])
    fingerprint = partition.get("development_partition_fingerprint")
    boundary = partition.get("development_boundary")

    partition_ready = (
        partition.get("study") == STUDY_ID
        and partition.get("partition_spec_version") == PARTITION_SPEC_VERSION
        and partition.get("development_partition_ready") is True
        and len(development_ids) == DEVELOPMENT_TARGET
        and bool(fingerprint)
        and isinstance(boundary, Mapping)
        and bool(boundary.get("resolved_at"))
        and bool(boundary.get("event_id"))
    )
    balance_ready = partition.get("development_analysis_eligible") is True
    development_outcome_opening_authorized = partition_ready and balance_ready

    if not partition_ready and not reasons:
        reasons.append("fixed_development_partition_not_verified")
    if partition_ready and not balance_ready and not reasons:
        reasons.append("fixed_development_cohort_failed_preregistered_group_balance")

    return {
        "study": STUDY_ID,
        "gate_spec_version": GATE_SPEC_VERSION,
        "partition_spec_version": PARTITION_SPEC_VERSION,
        "research_only": True,
        "label_blind_gate": True,
        "outcome_fields_read": False,
        "development_target": DEVELOPMENT_TARGET,
        "terminal_event_count": partition.get("terminal_event_count"),
        "development_partition_ready": partition_ready,
        "development_balance_ready": balance_ready,
        "development_event_ids": development_ids if partition_ready else [],
        "development_partition_fingerprint": fingerprint if partition_ready else None,
        "development_boundary": dict(boundary) if partition_ready else None,
        "development_balance": dict(partition.get("development_balance") or {}),
        "development_outcome_opening_authorized": development_outcome_opening_authorized,
        "validation_outcome_opening_authorized": False,
        "threshold_search_allowed": False,
        "promotion_allowed": False,
        "live_strategy_mutation": False,
        "execution_authorized": False,
        "reasons": reasons,
    }
