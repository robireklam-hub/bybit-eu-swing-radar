#!/usr/bin/env python3
"""One-shot production smoke for prospective swing-liquidity lifecycle adoption.

Research only. Creates one genuine label-blind forward capture through the existing
collector/persistence path, then fails closed unless the durable trial lifecycle is
at PIT_AUDIT_RECORDED, DATA_QUALITY_GATE_RECORDED, or the prospectively evidenced
LINEAGE_RECORDED state. It never opens outcomes, changes live thresholds, or
authorizes execution.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from research.swing_liquidity_shadow import collect_snapshot, persist_snapshot

EXPECTED_STUDY = "swing-liquidity-validation-v1"
ALLOWED_EVENT_TYPES = frozenset(("PIT_AUDIT_RECORDED", "DATA_QUALITY_GATE_RECORDED", "LINEAGE_RECORDED"))
ALLOWED_REASONS = frozenset(
    (
        "prospective_pit_audit",
        "insufficient_consecutive_post_pit_captures",
        "data_quality_gate_not_satisfied",
        "prospective_data_quality_gate",
        "waiting_for_fresh_post_data_quality_lineage_capture",
        "lineage_gate_not_satisfied",
        "prospective_lineage_gate",
        "lifecycle_already_beyond_lineage_adoption",
    )
)


def _valid_sha256(value: Any) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and all(ch in "0123456789abcdef" for ch in text)


def validate_persistence_identity(snapshot: dict[str, Any], result: dict[str, Any]) -> list[str]:
    """Verify that persistence metadata belongs to the exact collected snapshot."""
    errors: list[str] = []
    if not isinstance(result, dict):
        return ["persistence_response_not_object"]

    if result.get("captured_at") != snapshot.get("captured_at"):
        errors.append("captured_at_mismatch")

    expected_candidate_count = int(snapshot.get("candidate_count") or 0)
    try:
        persisted_candidate_count = int(result.get("candidate_count"))
    except (TypeError, ValueError):
        persisted_candidate_count = -1
    if persisted_candidate_count != expected_candidate_count:
        errors.append("candidate_count_mismatch")

    expected_orderbook_count = len(snapshot.get("orderbooks") or {})
    try:
        persisted_orderbook_count = int(result.get("orderbook_count"))
    except (TypeError, ValueError):
        persisted_orderbook_count = -1
    if persisted_orderbook_count != expected_orderbook_count:
        errors.append("orderbook_count_mismatch")

    expected_orderbook_error_count = len(snapshot.get("orderbook_errors") or {})
    try:
        persisted_orderbook_error_count = int(result.get("orderbook_error_count"))
    except (TypeError, ValueError):
        persisted_orderbook_error_count = -1
    if persisted_orderbook_error_count != expected_orderbook_error_count:
        errors.append("orderbook_error_count_mismatch")

    return errors


def validate_lifecycle_persistence(result: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if result.get("research_only") is not True:
        errors.append("research_only_not_true")
    if result.get("live_strategy_mutated") is not False:
        errors.append("live_strategy_mutated_not_false")
    if result.get("promotion_allowed") is not False:
        errors.append("promotion_allowed_not_false")
    if result.get("study") != EXPECTED_STUDY:
        errors.append("unexpected_study")
    if result.get("inserted") is not True:
        errors.append("fresh_capture_not_inserted")

    lifecycle = result.get("lifecycle_adoption")
    if not isinstance(lifecycle, dict):
        errors.append("missing_lifecycle_adoption")
        return errors
    for field, expected in (
        ("attempted", True),
        ("prospective_adoption", True),
        ("historical_backfill", False),
        ("research_only", True),
        ("live_strategy_mutated", False),
        ("production_eligibility_mutated", False),
        ("execution_authorized", False),
    ):
        if lifecycle.get(field) is not expected:
            errors.append(f"lifecycle_{field}_invalid")

    event_type = lifecycle.get("event_type")
    reason = lifecycle.get("reason")
    if event_type not in ALLOWED_EVENT_TYPES:
        errors.append(f"unexpected_current_lifecycle_event:{event_type}")
    if reason not in ALLOWED_REASONS:
        errors.append(f"unexpected_lifecycle_reason:{reason}")

    if reason == "prospective_pit_audit":
        if event_type != "PIT_AUDIT_RECORDED" or lifecycle.get("inserted") is not True:
            errors.append("pit_audit_transition_invalid")
        if not _valid_sha256(lifecycle.get("evidence_capture_fingerprint")):
            errors.append("invalid_evidence_capture_fingerprint")
    elif reason in {"insufficient_consecutive_post_pit_captures", "data_quality_gate_not_satisfied"}:
        if event_type != "PIT_AUDIT_RECORDED" or lifecycle.get("inserted") is not False:
            errors.append("waiting_data_quality_state_invalid")
        quality = lifecycle.get("data_quality")
        if not isinstance(quality, dict) or quality.get("ready") is not False:
            errors.append("waiting_data_quality_evidence_invalid")
    elif reason == "prospective_data_quality_gate":
        if event_type != "DATA_QUALITY_GATE_RECORDED" or lifecycle.get("inserted") is not True:
            errors.append("data_quality_transition_invalid")
        quality = lifecycle.get("data_quality")
        if not isinstance(quality, dict) or quality.get("ready") is not True:
            errors.append("data_quality_evidence_not_ready")
        elif not _valid_sha256(quality.get("evidence_window_fingerprint")):
            errors.append("invalid_data_quality_evidence_window_fingerprint")
    elif reason == "waiting_for_fresh_post_data_quality_lineage_capture":
        if event_type != "DATA_QUALITY_GATE_RECORDED" or lifecycle.get("inserted") is not False:
            errors.append("waiting_lineage_state_invalid")
    elif reason == "lineage_gate_not_satisfied":
        if event_type != "DATA_QUALITY_GATE_RECORDED" or lifecycle.get("inserted") is not False:
            errors.append("failed_lineage_state_invalid")
        lineage = lifecycle.get("lineage")
        if not isinstance(lineage, dict) or lineage.get("ready") is not False:
            errors.append("failed_lineage_evidence_invalid")
    elif reason == "prospective_lineage_gate":
        if event_type != "LINEAGE_RECORDED" or lifecycle.get("inserted") is not True:
            errors.append("lineage_transition_invalid")
        lineage = lifecycle.get("lineage")
        if not isinstance(lineage, dict) or lineage.get("ready") is not True:
            errors.append("lineage_evidence_not_ready")
        else:
            if not _valid_sha256(lineage.get("evidence_fingerprint")):
                errors.append("invalid_lineage_evidence_fingerprint")
            if not _valid_sha256(lineage.get("lineage_fingerprint")):
                errors.append("invalid_lineage_fingerprint")
    elif reason == "lifecycle_already_beyond_lineage_adoption":
        if event_type != "LINEAGE_RECORDED" or lifecycle.get("inserted") is not False:
            errors.append("recorded_lineage_state_invalid")

    return errors


def run_check(
    base_url: str,
    api_key: str,
    *,
    collect: Callable[[str, str], dict[str, Any]] = collect_snapshot,
    persist: Callable[[str, str, dict[str, Any]], dict[str, Any]] = persist_snapshot,
) -> int:
    snapshot = collect(base_url, api_key)
    result = persist(base_url, api_key, snapshot)
    identity_errors = validate_persistence_identity(snapshot, result)
    lifecycle = result.get("lifecycle_adoption") if isinstance(result, dict) else None
    safe = {
        "study": result.get("study") if isinstance(result, dict) else None,
        "captured_at": result.get("captured_at") if isinstance(result, dict) else None,
        "inserted": result.get("inserted") if isinstance(result, dict) else None,
        "candidate_count": result.get("candidate_count") if isinstance(result, dict) else None,
        "orderbook_count": result.get("orderbook_count") if isinstance(result, dict) else None,
        "orderbook_error_count": result.get("orderbook_error_count") if isinstance(result, dict) else None,
        "persistence_identity_verified": not identity_errors,
        "lifecycle_adoption": lifecycle,
    }
    print("SWING_LIQUIDITY_LIFECYCLE_SMOKE=" + json.dumps(safe, sort_keys=True, default=str))
    if identity_errors:
        for error in identity_errors:
            print(f"FAIL persistence_identity_{error}")
        return 1
    errors = validate_lifecycle_persistence(result)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print("SWING LIQUIDITY PROSPECTIVE LIFECYCLE AND PERSISTENCE IDENTITY VERIFIED THROUGH LINEAGE GATE.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    if not base_url or not api_key:
        print("FAIL required production configuration is missing")
        return 1
    try:
        return run_check(base_url, api_key)
    except Exception as exc:
        print(f"FAIL request_error={type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
