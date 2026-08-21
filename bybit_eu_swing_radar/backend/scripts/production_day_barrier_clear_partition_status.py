"""Production verifier for the label-blind day barrier-clear 60/40 partition."""
from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen
from typing import Any


def validate_partition_observer(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    cumulative = payload.get("cumulative") or {}
    terminal_count = sum(int(cumulative.get(key, 0) or 0) for key in (
        "cleared", "invalidated_boundary", "invalidated_structure"
    ))
    partition = payload.get("partition") or {}
    expected = {
        "study": "day-barrier-clear-rearm-v1",
        "partition_spec_version": "day-barrier-clear-partition-v1",
        "research_only": True,
        "label_blind_partition": True,
        "outcome_fields_used": False,
        "development_target": 60,
        "validation_target": 40,
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
    development_ids = partition.get("development_event_ids") or []
    validation_ids = partition.get("validation_event_ids") or []
    if partition.get("development_partition_ready"):
        if len(development_ids) != 60 or not partition.get("development_partition_fingerprint"):
            errors.append("development partition identity invalid")
    elif development_ids or partition.get("development_partition_fingerprint") is not None:
        errors.append("partial development partition leaked")
    if partition.get("validation_partition_ready"):
        if len(validation_ids) != 40 or not partition.get("validation_partition_fingerprint"):
            errors.append("validation partition identity invalid")
    elif validation_ids or partition.get("validation_partition_fingerprint") is not None:
        errors.append("partial validation partition leaked")
    if set(development_ids).intersection(validation_ids):
        errors.append("development validation overlap")
    if partition.get("development_analysis_eligible") and not partition.get("development_partition_ready"):
        errors.append("development eligibility before partition readiness")
    return {"ok": not errors, "errors": errors, "terminal_count": terminal_count, "partition": partition}


def main() -> int:
    base = os.environ["PRODUCTION_RADAR_API_BASE_URL"].rstrip("/")
    key = os.environ["PRODUCTION_RADAR_API_KEY"]
    request = Request(
        base + "/v1/day-trade/research/barrier-clear-rearm/observer-status",
        headers={"Accept": "application/json", "X-Radar-Key": key, "User-Agent": "barrier-partition-smoke/1"},
    )
    with urlopen(request, timeout=25) as response:
        payload = json.loads(response.read().decode())
    evidence = validate_partition_observer(payload)
    print("DAY_BARRIER_CLEAR_PARTITION_STATUS=" + json.dumps(evidence, sort_keys=True, default=str), flush=True)
    if not evidence["ok"]:
        return 1
    print("DAY BARRIER CLEAR LABEL-BLIND PARTITION STATUS VERIFIED.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
