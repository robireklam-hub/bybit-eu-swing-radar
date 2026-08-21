#!/usr/bin/env python3
"""Read-only exact-SHA production verification for policy catalyst source observability v1."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from research.policy_catalyst_sources_v1 import source_registry

EXPECTED_OBSERVABILITY_SPEC = "policy-catalyst-source-observability-v1"
EXPECTED_EVENT_STORE_SPEC = "policy-catalyst-event-store-v1"


def fetch_json(url: str, api_key: str, timeout: float) -> dict[str, Any]:
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "bybit-eu-policy-catalyst-source-observability-smoke/1",
            "X-Radar-Key": api_key,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _expected_sources() -> dict[str, bool]:
    return {str(row["provider_code"]): bool(row.get("enabled")) for row in source_registry()}


def validate_status(status: dict[str, Any], expected_sha: str) -> tuple[bool, str, dict[str, Any]]:
    if status.get("research_only") is not True:
        return False, "research_only_not_true", {}
    if status.get("context_only") is not True or status.get("hard_gate") is not False:
        return False, "context_only_contract_invalid", {}
    if status.get("live_strategy_mutated") is not False:
        return False, "live_strategy_mutated_not_false", {}
    if status.get("freshness") != "FRESH":
        return False, "latest_capture_not_fresh", {}

    latest = status.get("latest_capture") or {}
    if latest.get("source_commit_sha") != expected_sha:
        return False, "latest_capture_sha_mismatch", {}

    observability = status.get("source_observability_v1")
    if not isinstance(observability, dict):
        return False, "source_observability_missing", {}
    if observability.get("spec_version") != EXPECTED_OBSERVABILITY_SPEC:
        return False, "source_observability_spec_mismatch", {}
    if observability.get("event_store_spec_version") != EXPECTED_EVENT_STORE_SPEC:
        return False, "source_observability_event_store_spec_mismatch", {}
    if observability.get("research_only") is not True:
        return False, "source_observability_research_only_not_true", {}
    if observability.get("context_only") is not True or observability.get("hard_gate") is not False:
        return False, "source_observability_context_only_contract_invalid", {}
    if observability.get("live_strategy_mutated") is not False:
        return False, "source_observability_live_strategy_mutated_not_false", {}

    rows = observability.get("sources")
    if not isinstance(rows, list) or not rows:
        return False, "source_observability_rows_missing", {}
    if any(not isinstance(row, dict) for row in rows):
        return False, "source_observability_row_invalid", {}

    actual_codes = [str(row.get("provider_code") or "") for row in rows]
    if any(not code for code in actual_codes):
        return False, "source_observability_provider_code_missing", {}
    if len(actual_codes) != len(set(actual_codes)):
        return False, "source_observability_duplicate_provider", {}

    expected = _expected_sources()
    if set(actual_codes) != set(expected):
        return False, "source_observability_provider_set_mismatch", {}

    enabled_count = 0
    available_count = 0
    fresh_count = 0
    persisted_event_source_count = 0
    operationally_unavailable: list[str] = []
    pending_no_event: list[str] = []

    for row in rows:
        code = str(row["provider_code"])
        enabled = expected[code]
        if row.get("enabled") is not enabled:
            return False, "source_observability_enabled_mismatch", {}
        if row.get("context_only") is not True or row.get("hard_gate") is not False:
            return False, "source_row_context_only_contract_invalid", {}
        for field in ("score_mutation", "ranking_mutation", "eligibility_mutation", "execution_mutation"):
            if row.get(field) is not False:
                return False, f"source_row_{field}_not_false", {}
        if row.get("event_store_spec_version") != EXPECTED_EVENT_STORE_SPEC:
            return False, "source_row_event_store_spec_mismatch", {}
        try:
            event_count = int(row.get("event_store_event_count") or 0)
        except (TypeError, ValueError):
            return False, "source_row_event_count_invalid", {}
        if event_count < 0:
            return False, "source_row_event_count_negative", {}

        collection_status = row.get("collection_status")
        collection_freshness = row.get("collection_freshness")
        event_store_status = row.get("event_store_status")
        if enabled:
            enabled_count += 1
            if collection_status not in {"AVAILABLE", "UNAVAILABLE"}:
                return False, "enabled_source_collection_status_invalid", {}
            if collection_freshness not in {"FRESH", "STALE", "UNAVAILABLE"}:
                return False, "enabled_source_collection_freshness_invalid", {}
            if event_store_status not in {
                "PERSISTED_EVENT_OBSERVED",
                "PENDING_NO_TIMESTAMPED_EVENT",
                "UNAVAILABLE_SOURCE_COLLECTION",
            }:
                return False, "enabled_source_event_store_status_invalid", {}
            if collection_status == "AVAILABLE":
                available_count += 1
            else:
                operationally_unavailable.append(code)
            if collection_freshness == "FRESH":
                fresh_count += 1
            if event_count > 0:
                persisted_event_source_count += 1
            if event_store_status == "PENDING_NO_TIMESTAMPED_EVENT":
                pending_no_event.append(code)
        else:
            if collection_status != "NOT_CONFIGURED" or event_store_status != "NOT_CONFIGURED":
                return False, "disabled_source_not_configured_contract_invalid", {}

    count_fields = {
        "enabled_source_count": enabled_count,
        "available_source_count": available_count,
        "fresh_source_count": fresh_count,
        "persisted_event_source_count": persisted_event_source_count,
    }
    for field, expected_count in count_fields.items():
        if observability.get(field) != expected_count:
            return False, f"source_observability_{field}_mismatch", {}

    summary = {
        "spec_version": EXPECTED_OBSERVABILITY_SPEC,
        "event_store_spec_version": EXPECTED_EVENT_STORE_SPEC,
        **count_fields,
        "operationally_unavailable_sources": sorted(operationally_unavailable),
        "pending_no_timestamped_event_sources": sorted(pending_no_event),
        "provider_codes": sorted(actual_codes),
    }
    return True, "ok", summary


def run_smoke(
    base_url: str,
    api_key: str,
    expected_sha: str,
    *,
    fetch: Callable[[str, str, float], dict[str, Any]] = fetch_json,
) -> int:
    try:
        version = fetch(f"{base_url.rstrip('/')}/version", api_key, 20.0)
    except Exception as exc:
        print(f"FAIL phase=version error_type={type(exc).__name__}")
        return 1
    if version.get("commit_sha") != expected_sha:
        print("FAIL phase=version reason=expected_commit_not_deployed")
        return 1

    try:
        status = fetch(f"{base_url.rstrip('/')}/v1/research/policy-catalyst/status", api_key, 30.0)
    except Exception as exc:
        print(f"FAIL phase=status error_type={type(exc).__name__}")
        return 1

    ok, reason, summary = validate_status(status, expected_sha)
    if not ok:
        print(f"FAIL phase=source-observability-v1 reason={reason}")
        return 1

    print("POLICY_CATALYST_SOURCE_OBSERVABILITY_V1=" + json.dumps(summary, sort_keys=True))
    print("POLICY CATALYST SOURCE OBSERVABILITY V1 VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required policy-catalyst source-observability smoke configuration is missing")
        return 1
    return run_smoke(base_url, api_key, expected_sha)


if __name__ == "__main__":
    sys.exit(main())
