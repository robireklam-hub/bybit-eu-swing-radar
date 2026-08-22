"""Read-only production verifier for the barrier-clear DEVELOPMENT opening gate.

Consumes only label-blind partition metadata already published by the observer.
It never requests outcomes and never opens VALIDATION, threshold search,
promotion, live strategy mutation, or execution.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.request import Request, urlopen

GATE_SPEC_VERSION = "day-barrier-clear-development-gate-v1"
PARTITION_SPEC_VERSION = "day-barrier-clear-partition-v1"
STUDY_ID = "day-barrier-clear-rearm-v1"
DEVELOPMENT_TARGET = 60


def validate_development_gate_observer(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    partition = payload.get("partition") or {}
    cumulative = payload.get("cumulative") or {}
    terminal_count = sum(
        int(cumulative.get(key, 0) or 0)
        for key in ("cleared", "invalidated_boundary", "invalidated_structure")
    )

    expected = {
        "study": STUDY_ID,
        "partition_spec_version": PARTITION_SPEC_VERSION,
        "research_only": True,
        "label_blind_partition": True,
        "outcome_fields_used": False,
        "development_target": DEVELOPMENT_TARGET,
        "outcome_visible": False,
        "threshold_search_allowed": False,
        "promotion_allowed": False,
        "live_strategy_mutation": False,
        "execution_authorized": False,
    }
    for key, value in expected.items():
        if partition.get(key) != value:
            errors.append(f"partition.{key} mismatch")

    if partition.get("terminal_event_count") != terminal_count:
        errors.append("partition terminal count mismatch")

    development_ids = list(partition.get("development_event_ids") or [])
    fingerprint = partition.get("development_partition_fingerprint")
    boundary = partition.get("development_boundary") or {}
    balance = partition.get("development_balance") or {}

    if partition.get("development_partition_ready") is not True:
        errors.append("fixed development partition not ready")
    if len(development_ids) != DEVELOPMENT_TARGET:
        errors.append("development event identity count mismatch")
    if not fingerprint:
        errors.append("development fingerprint missing")
    if not boundary.get("resolved_at") or not boundary.get("event_id"):
        errors.append("development composite boundary missing")

    minimums = {
        "cleared": "minimum_cleared",
        "noncleared": "minimum_noncleared",
        "long": "minimum_long",
        "short": "minimum_short",
    }
    for actual_key, minimum_key in minimums.items():
        actual = int(balance.get(actual_key, 0) or 0)
        minimum = int(balance.get(minimum_key, 0) or 0)
        if minimum <= 0:
            errors.append(f"development balance minimum missing: {minimum_key}")
        elif actual < minimum:
            errors.append(f"development balance failed: {actual_key}")

    balance_ready = partition.get("development_analysis_eligible") is True
    if not balance_ready:
        errors.append("development analysis eligibility not authorized")

    gate_authorized = not errors
    return {
        "ok": gate_authorized,
        "errors": errors,
        "study": STUDY_ID,
        "gate_spec_version": GATE_SPEC_VERSION,
        "partition_spec_version": PARTITION_SPEC_VERSION,
        "research_only": True,
        "label_blind_gate": True,
        "outcome_fields_read": False,
        "terminal_event_count": terminal_count,
        "development_event_count": len(development_ids),
        "development_partition_fingerprint": fingerprint if gate_authorized else None,
        "development_boundary": dict(boundary) if gate_authorized else None,
        "development_balance": dict(balance),
        "development_outcome_opening_authorized": gate_authorized,
        "validation_outcome_opening_authorized": False,
        "threshold_search_allowed": False,
        "promotion_allowed": False,
        "live_strategy_mutation": False,
        "execution_authorized": False,
    }


def main() -> int:
    base = os.environ["PRODUCTION_RADAR_API_BASE_URL"].rstrip("/")
    key = os.environ["PRODUCTION_RADAR_API_KEY"]
    request = Request(
        base + "/v1/day-trade/research/barrier-clear-rearm/observer-status",
        headers={
            "Accept": "application/json",
            "X-Radar-Key": key,
            "User-Agent": "barrier-development-gate-smoke/1",
        },
    )
    with urlopen(request, timeout=25) as response:
        payload = json.loads(response.read().decode())
    evidence = validate_development_gate_observer(payload)
    print(
        "DAY_BARRIER_CLEAR_DEVELOPMENT_GATE_STATUS="
        + json.dumps(evidence, sort_keys=True, default=str),
        flush=True,
    )
    if not evidence["ok"]:
        return 1
    print("DAY BARRIER CLEAR DEVELOPMENT GATE VERIFIED.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
