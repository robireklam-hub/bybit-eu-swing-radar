"""Frozen, outcome-blind lineage contract for swing liquidity validation.

Research only. The lineage milestone proves that a fresh forward capture remains
bound to the preregistered trial identity, PIT provenance, source commit and
capture/order-book grain after the data-quality lifecycle gate. It never reads
outcomes, tunes thresholds, or mutates live strategy, eligibility or execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from research.research_governance import PIT_VERSION
from research.research_lifecycle_ledger import canonical_fingerprint

LINEAGE_SPEC_VERSION = "swing-liquidity-lineage-v1"
# Frozen before activation. Captures persisted before this boundary can never be
# used to reconstruct the lineage milestone retroactively.
LINEAGE_FORWARD_START_UTC = datetime(2026, 8, 21, 4, 53, 33, tzinfo=timezone.utc)
DATASET_ID = "swing-liquidity-forward-captures"
CAPTURE_GRAIN = "one immutable label-blind swing-liquidity capture"


def spec() -> dict[str, Any]:
    return {
        "spec_version": LINEAGE_SPEC_VERSION,
        "dataset_id": DATASET_ID,
        "capture_grain": CAPTURE_GRAIN,
        "research_only": True,
        "label_blind": True,
        "outcome_fields_used": False,
        "threshold_search_allowed": False,
        "historical_backfill_allowed": False,
        "forward_start_utc": LINEAGE_FORWARD_START_UTC.isoformat(),
        "required_provenance_version": PIT_VERSION,
        "required_identity_fields": ["trial_id", "trial_fingerprint", "captured_at", "inserted_at"],
        "required_lineage_fields": [
            "feature_available_at",
            "source_commit_sha",
            "candidate_count",
            "orderbook_count",
            "orderbook_error_count",
        ],
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
        "execution_authorized": False,
    }


def _aware_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    return value.astimezone(timezone.utc)


def _valid_commit_sha(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 40 and all(ch in "0123456789abcdef" for ch in text)


def evaluate_lineage_capture(
    row: Mapping[str, Any] | None,
    *,
    trial_id: str,
    trial_fingerprint: str,
    data_quality_event_fingerprint: str,
) -> dict[str, Any]:
    record = dict(row or {})
    failures: list[str] = []

    if str(record.get("trial_id") or "") != trial_id:
        failures.append("trial_id_mismatch")
    if str(record.get("trial_fingerprint") or "") != trial_fingerprint:
        failures.append("trial_fingerprint_mismatch")
    if str(record.get("provenance_version") or "") != PIT_VERSION:
        failures.append("provenance_version_mismatch")

    inserted_at = _aware_utc(record.get("inserted_at"))
    if inserted_at is None:
        failures.append("inserted_at_not_timezone_aware")
    elif inserted_at < LINEAGE_FORWARD_START_UTC:
        failures.append("predates_lineage_forward_start")

    captured_at = _aware_utc(record.get("captured_at"))
    if captured_at is None:
        failures.append("captured_at_not_timezone_aware")
    feature_available_at = _aware_utc(record.get("feature_available_at"))
    if feature_available_at is None:
        failures.append("feature_available_at_not_timezone_aware")
    elif captured_at is not None and feature_available_at < captured_at:
        failures.append("feature_available_before_capture")

    if not _valid_commit_sha(record.get("source_commit_sha")):
        failures.append("invalid_source_commit_sha")

    try:
        candidate_count = int(record.get("candidate_count"))
        orderbook_count = int(record.get("orderbook_count"))
        orderbook_error_count = int(record.get("orderbook_error_count"))
    except (TypeError, ValueError):
        candidate_count = orderbook_count = orderbook_error_count = -1
        failures.append("invalid_capture_counts")

    if candidate_count <= 0:
        failures.append("candidate_count_not_positive")
    if orderbook_count != candidate_count:
        failures.append("orderbook_coverage_incomplete")
    if orderbook_error_count != 0:
        failures.append("orderbook_errors_present")

    if len(str(data_quality_event_fingerprint or "")) != 64:
        failures.append("invalid_data_quality_event_fingerprint")

    evidence = {
        "lineage_spec_version": LINEAGE_SPEC_VERSION,
        "dataset_id": DATASET_ID,
        "trial_id": trial_id,
        "trial_fingerprint": trial_fingerprint,
        "data_quality_event_fingerprint": data_quality_event_fingerprint,
        "captured_at": str(record.get("captured_at")),
        "inserted_at": str(record.get("inserted_at")),
        "feature_available_at": str(record.get("feature_available_at")),
        "provenance_version": str(record.get("provenance_version") or ""),
        "source_commit_sha": str(record.get("source_commit_sha") or ""),
        "candidate_count": candidate_count,
        "orderbook_count": orderbook_count,
        "orderbook_error_count": orderbook_error_count,
        "label_blind": True,
        "outcome_fields_used": False,
    }

    if failures:
        return {
            "ready": False,
            "reason": "lineage_gate_not_satisfied",
            "failures": failures,
            "evidence": evidence,
            "spec": spec(),
        }

    evidence_fingerprint = canonical_fingerprint(evidence)
    return {
        "ready": True,
        "reason": "lineage_gate_satisfied",
        "evidence": evidence,
        "evidence_fingerprint": evidence_fingerprint,
        "lineage_fingerprint": canonical_fingerprint(
            {
                "spec_version": LINEAGE_SPEC_VERSION,
                "forward_start_utc": LINEAGE_FORWARD_START_UTC.isoformat(),
                "evidence_fingerprint": evidence_fingerprint,
            }
        ),
        "spec": spec(),
    }
