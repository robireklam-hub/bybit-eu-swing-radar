#!/usr/bin/env python3
"""One-shot production smoke for prospective swing-liquidity lifecycle adoption.

Research only. Creates one genuine label-blind forward capture through the existing
collector/persistence path, then fails closed unless the durable trial lifecycle is
exactly at PIT_AUDIT_RECORDED. It never opens outcomes, changes live thresholds,
or authorizes execution.
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
EXPECTED_EVENT_TYPE = "PIT_AUDIT_RECORDED"
ALLOWED_REASONS = frozenset(("prospective_pit_audit", "lifecycle_already_beyond_pit_adoption"))


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
    if lifecycle.get("attempted") is not True:
        errors.append("lifecycle_not_attempted")
    if lifecycle.get("prospective_adoption") is not True:
        errors.append("prospective_adoption_not_true")
    if lifecycle.get("historical_backfill") is not False:
        errors.append("historical_backfill_not_false")
    if lifecycle.get("research_only") is not True:
        errors.append("lifecycle_research_only_not_true")
    if lifecycle.get("live_strategy_mutated") is not False:
        errors.append("lifecycle_live_strategy_mutated_not_false")
    if lifecycle.get("production_eligibility_mutated") is not False:
        errors.append("production_eligibility_mutated_not_false")
    if lifecycle.get("execution_authorized") is not False:
        errors.append("execution_authorized_not_false")
    if lifecycle.get("event_type") != EXPECTED_EVENT_TYPE:
        errors.append(f"unexpected_current_lifecycle_event:{lifecycle.get('event_type')}")
    if lifecycle.get("reason") not in ALLOWED_REASONS:
        errors.append(f"unexpected_lifecycle_reason:{lifecycle.get('reason')}")
    if lifecycle.get("reason") == "prospective_pit_audit":
        if lifecycle.get("inserted") is not True:
            errors.append("pit_audit_transition_not_inserted")
        fingerprint = str(lifecycle.get("evidence_capture_fingerprint") or "")
        if len(fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in fingerprint.lower()):
            errors.append("invalid_evidence_capture_fingerprint")
    elif lifecycle.get("inserted") is not False:
        errors.append("already_recorded_pit_should_not_reinsert")
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
    lifecycle = result.get("lifecycle_adoption") if isinstance(result, dict) else None
    safe = {
        "study": result.get("study") if isinstance(result, dict) else None,
        "captured_at": result.get("captured_at") if isinstance(result, dict) else None,
        "inserted": result.get("inserted") if isinstance(result, dict) else None,
        "candidate_count": result.get("candidate_count") if isinstance(result, dict) else None,
        "orderbook_count": result.get("orderbook_count") if isinstance(result, dict) else None,
        "orderbook_error_count": result.get("orderbook_error_count") if isinstance(result, dict) else None,
        "lifecycle_adoption": lifecycle,
    }
    print("SWING_LIQUIDITY_LIFECYCLE_SMOKE=" + json.dumps(safe, sort_keys=True, default=str))
    errors = validate_lifecycle_persistence(result)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print("SWING LIQUIDITY PROSPECTIVE PIT LIFECYCLE VERIFIED.")
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
