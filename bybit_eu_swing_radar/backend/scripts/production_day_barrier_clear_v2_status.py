"""Read-only production verifier for activated barrier-clear v2 status.

Reads the observer cache only. It verifies prospective activation, no v1-parent
reuse, side-stratified DEVELOPMENT/VALIDATION quotas and closed outcome/live
mutation firewalls. It never requests or exposes outcome data.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from research.day_barrier_clear_rearm_v2_activation import ACTIVATION_BOUNDARY
from research.day_barrier_clear_rearm_v2_status import STATUS_SPEC_VERSION


def validate_v2_status(payload: dict[str, Any], *, expected_sha: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    v2 = payload.get("v2") or {}
    expected = {
        "status_spec_version": STATUS_SPEC_VERSION,
        "trial_id": "day-barrier-clear-rearm-v2",
        "activated": True,
        "activation_boundary": ACTIVATION_BOUNDARY,
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "historical_backfill_allowed": False,
        "v1_event_reuse_allowed": False,
        "pre_activation_parent_reuse_allowed": False,
        "development_target": 60,
        "development_per_side": 30,
        "validation_target": 40,
        "validation_per_side": 20,
        "outcome_visible": False,
        "threshold_search_allowed": False,
        "promotion_allowed": False,
        "execution_authorized": False,
        "live_strategy_mutated": False,
    }
    for key, value in expected.items():
        if v2.get(key) != value:
            errors.append(f"v2.{key} mismatch")

    source_sha = str(v2.get("source_commit_sha") or "")
    if expected_sha and source_sha != expected_sha:
        errors.append("v2.source_commit_sha mismatch")

    eligible = int(v2.get("eligible_terminal_event_count", 0) or 0)
    long_count = int(v2.get("eligible_long_count", 0) or 0)
    short_count = int(v2.get("eligible_short_count", 0) or 0)
    if long_count + short_count != eligible:
        errors.append("eligible side partition mismatch")

    development_ready = v2.get("development_ready") is True
    expected_development_ready = long_count >= 30 and short_count >= 30
    if development_ready != expected_development_ready:
        errors.append("DEVELOPMENT readiness does not match eligible side quotas")
    development_count = int(v2.get("development_event_count", 0) or 0)
    if development_ready:
        if development_count != 60:
            errors.append("ready DEVELOPMENT count mismatch")
        if int(v2.get("development_long_count", 0) or 0) != 30:
            errors.append("ready DEVELOPMENT long quota mismatch")
        if int(v2.get("development_short_count", 0) or 0) != 30:
            errors.append("ready DEVELOPMENT short quota mismatch")
        if not v2.get("development_fingerprint"):
            errors.append("ready DEVELOPMENT fingerprint missing")
    else:
        if development_count != 0 or v2.get("development_fingerprint") is not None:
            errors.append("partial DEVELOPMENT freeze detected")

    validation_ready = v2.get("validation_ready") is True
    expected_validation_ready = long_count >= 50 and short_count >= 50
    if validation_ready != expected_validation_ready:
        errors.append("VALIDATION readiness does not match eligible side quotas")
    validation_count = int(v2.get("validation_event_count", 0) or 0)
    if validation_ready:
        if not development_ready:
            errors.append("VALIDATION ready before DEVELOPMENT")
        if validation_count != 40:
            errors.append("ready VALIDATION count mismatch")
        if int(v2.get("validation_long_count", 0) or 0) != 20:
            errors.append("ready VALIDATION long quota mismatch")
        if int(v2.get("validation_short_count", 0) or 0) != 20:
            errors.append("ready VALIDATION short quota mismatch")
        if not v2.get("validation_fingerprint"):
            errors.append("ready VALIDATION fingerprint missing")
    else:
        if validation_count != 0 or v2.get("validation_fingerprint") is not None:
            errors.append("partial VALIDATION freeze detected")

    return {
        "ok": not errors,
        "errors": errors,
        "source_commit_sha": source_sha,
        "activation_boundary": v2.get("activation_boundary"),
        "eligible_terminal_event_count": eligible,
        "eligible_long_count": long_count,
        "eligible_short_count": short_count,
        "excluded_pre_activation_parent_count": int(v2.get("excluded_pre_activation_parent_count", 0) or 0),
        "development_ready": development_ready,
        "development_event_count": development_count,
        "validation_ready": validation_ready,
        "validation_event_count": validation_count,
        "outcome_visible": False,
        "threshold_search_allowed": False,
        "promotion_allowed": False,
        "execution_authorized": False,
    }


def main() -> int:
    base = os.environ["PRODUCTION_RADAR_API_BASE_URL"].rstrip("/")
    key = os.environ["PRODUCTION_RADAR_API_KEY"]
    expected_sha = os.environ.get("EXPECTED_SHA")
    request = Request(
        base + "/v1/day-trade/research/barrier-clear-rearm/observer-status",
        headers={
            "Accept": "application/json",
            "X-Radar-Key": key,
            "User-Agent": "barrier-v2-status-smoke/1",
        },
    )
    with urlopen(request, timeout=25) as response:
        payload = json.loads(response.read().decode())
    evidence = validate_v2_status(payload, expected_sha=expected_sha)
    print("DAY_BARRIER_CLEAR_V2_STATUS=" + json.dumps(evidence, sort_keys=True), flush=True)
    if not evidence["ok"]:
        return 1
    print("DAY BARRIER CLEAR V2 STATUS VERIFIED CLOSED AND PROSPECTIVE.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
