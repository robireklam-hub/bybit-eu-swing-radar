"""Research-governance primitives for preregistered, point-in-time studies.

This module is deliberately research-only. It provides deterministic trial
fingerprints and local collector-time provenance; it never scores candidates,
changes eligibility, permits promotion, or touches execution state.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

PIT_VERSION = "pit-v1"
LEGACY_PROVENANCE_VERSION = "legacy-captured-at-v0"

_TRIALS: dict[str, dict[str, Any]] = {
    "swing-liquidity-validation-v1": {
        "trial_id": "swing-liquidity-validation-v1",
        "research_family": "swing-liquidity",
        "revision": 1,
        "preregistered": True,
        "frozen": True,
        "development_target_matured_events": 60,
        "validation_target_matured_events": 40,
        "pre_trigger_max_snapshot_age_minutes": 90,
        "primary_notional_usdc": 500.0,
        "event_identity": "symbol_side_first_qualifying_4h_trigger_bar",
        "preregistration": "backend/research/SWING_LIQUIDITY_VALIDATION_V1.md",
    },
    "day-barrier-clear-rearm-v1": {
        "trial_id": "day-barrier-clear-rearm-v1",
        "research_family": "day-barrier-clear-rearm",
        "revision": 1,
        "preregistered": True,
        "frozen": True,
        "parent_strategy_version": "0.7.5",
        "quote_asset": "USDC",
        "long_execution": "USDC_SPOT",
        "short_execution": "VERIFIED_BORROWABLE_USDC_SPOT_MARGIN_ONLY",
        "perpetual_execution": False,
        "derivatives_context_only": True,
        "missing_derivatives_hard_gate": False,
        "minimum_setup_score": 70.0,
        "minimum_expansion_score": 55.0,
        "minimum_side_direction_score": 35.0,
        "minimum_quality_score": 65.0,
        "minimum_rr_without_barrier": 1.8,
        "barrier_clear_confirmation": "CLOSED_5M_BEYOND_FROZEN_BARRIER_WHILE_ORIGINAL_BOUNDARY_HELD",
        "fresh_geometry_required": True,
        "outcome_visibility": "LOCKED_UNTIL_PREREGISTERED_DEVELOPMENT_GATE",
        "validation_policy": "DEVELOPMENT_RULE_FROZEN_BEFORE_UNTOUCHED_VALIDATION",
        "preregistration": "backend/research/DAY_BARRIER_CLEAR_REARM_V1.md",
    },
}


def _timestamp(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise ValueError(f"{field} is required")
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def manifest_fingerprint(manifest: dict[str, Any]) -> str:
    """Return a deterministic SHA-256 fingerprint for a frozen trial manifest."""
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def trial_manifest(study: str) -> dict[str, Any]:
    try:
        return deepcopy(_TRIALS[study])
    except KeyError as exc:
        raise ValueError(f"unregistered research study: {study}") from exc


def trial_fingerprint(study: str) -> str:
    return manifest_fingerprint(trial_manifest(study))


def validate_trial_registration(
    study: str,
    manifest: dict[str, Any],
    fingerprint: str,
) -> None:
    """Fail closed if a posted trial differs from the frozen in-code registration."""
    expected = trial_manifest(study)
    expected_fingerprint = manifest_fingerprint(expected)
    if manifest != expected:
        raise ValueError("trial manifest does not match the frozen registry")
    if fingerprint != expected_fingerprint:
        raise ValueError("trial fingerprint does not match the frozen registry")
    if manifest.get("trial_id") != study:
        raise ValueError("trial_id does not match study")


def validate_point_in_time(
    provenance: dict[str, Any],
    *,
    decision_time: datetime | str | None = None,
) -> datetime:
    """Validate local collector stage ordering and return feature availability time.

    Upstream source timestamps are intentionally metadata-only here: provider clock
    skew must not invalidate an otherwise ordered local collection pipeline.
    """
    if not isinstance(provenance, dict) or provenance.get("version") != PIT_VERSION:
        raise ValueError("point-in-time provenance version must be pit-v1")
    fields = (
        "collection_started_at",
        "scan_received_at",
        "orderbooks_completed_at",
        "feature_computed_at",
        "feature_available_at",
    )
    stages = [_timestamp(provenance.get(field), field) for field in fields]
    for previous, current in zip(stages, stages[1:]):
        if current < previous:
            raise ValueError("local point-in-time stages are out of order")
    available_at = stages[-1]
    if decision_time is not None and available_at > _timestamp(decision_time, "decision_time"):
        raise ValueError("feature was not available at decision_time")
    return available_at


def build_point_in_time_provenance(
    *,
    collection_started_at: datetime | str,
    scan_received_at: datetime | str,
    orderbooks_completed_at: datetime | str,
    feature_computed_at: datetime | str,
    feature_available_at: datetime | str,
    scan_source_data_as_of: Any = None,
) -> dict[str, Any]:
    provenance = {
        "version": PIT_VERSION,
        "collection_started_at": _timestamp(collection_started_at, "collection_started_at").isoformat(),
        "scan_received_at": _timestamp(scan_received_at, "scan_received_at").isoformat(),
        "orderbooks_completed_at": _timestamp(orderbooks_completed_at, "orderbooks_completed_at").isoformat(),
        "feature_computed_at": _timestamp(feature_computed_at, "feature_computed_at").isoformat(),
        "feature_available_at": _timestamp(feature_available_at, "feature_available_at").isoformat(),
        "scan_source_data_as_of": scan_source_data_as_of,
    }
    validate_point_in_time(provenance)
    return provenance


def snapshot_governance_metadata(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return effective availability while preserving legacy captures as unverified."""
    captured_at = _timestamp(snapshot.get("captured_at"), "captured_at")
    provenance = snapshot.get("point_in_time")
    if provenance is None:
        return {
            "provenance_version": LEGACY_PROVENANCE_VERSION,
            "point_in_time_verified": False,
            "feature_available_at": captured_at,
            "trial_id": None,
            "trial_fingerprint": None,
        }

    available_at = validate_point_in_time(provenance)
    top_level_available = snapshot.get("feature_available_at")
    if top_level_available is None or _timestamp(top_level_available, "feature_available_at") != available_at:
        raise ValueError("top-level feature_available_at must match point_in_time provenance")
    manifest = snapshot.get("trial_manifest")
    fingerprint = snapshot.get("trial_fingerprint")
    trial_id = snapshot.get("trial_id")
    if not isinstance(manifest, dict) or not isinstance(fingerprint, str) or not isinstance(trial_id, str):
        raise ValueError("pit-v1 snapshots require a registered trial manifest and fingerprint")
    validate_trial_registration(str(snapshot.get("study") or ""), manifest, fingerprint)
    if trial_id != manifest.get("trial_id"):
        raise ValueError("snapshot trial_id does not match trial manifest")
    return {
        "provenance_version": PIT_VERSION,
        "point_in_time_verified": True,
        "feature_available_at": available_at,
        "trial_id": trial_id,
        "trial_fingerprint": fingerprint,
    }
