#!/usr/bin/env python3
"""Read-only exact-SHA production verification for policy catalyst event-store v1."""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable
from urllib.request import Request, urlopen

EXPECTED_EVENT_STORE_SPEC = "policy-catalyst-event-store-v1"


def fetch_json(url: str, api_key: str, timeout: float) -> dict[str, Any]:
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "bybit-eu-policy-catalyst-event-store-smoke/1",
            "X-Radar-Key": api_key,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


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

    events = list(latest.get("events") or [])
    timestamped = [event for event in events if event.get("published_at")]
    persisted = []
    invalid = []
    for event in timestamped:
        marker = event.get("event_store_v1") or {}
        if (
            marker.get("status") == "PERSISTED"
            and marker.get("spec_version") == EXPECTED_EVENT_STORE_SPEC
            and bool(marker.get("event_id"))
        ):
            persisted.append(event)
        else:
            invalid.append(event)

    if invalid:
        return False, "timestamped_event_missing_v1_persistence", {}

    recent = list(status.get("recent_24h_events") or [])
    recent_persisted = [
        event
        for event in recent
        if (event.get("event_store_v1") or {}).get("status") == "PERSISTED"
        and (event.get("event_store_v1") or {}).get("spec_version") == EXPECTED_EVENT_STORE_SPEC
        and bool((event.get("event_store_v1") or {}).get("event_id"))
    ]
    if not persisted and not recent_persisted:
        return False, "no_v1_persisted_event_observed", {}

    summary = {
        "latest_timestamped_event_count": len(timestamped),
        "latest_v1_persisted_event_count": len(persisted),
        "recent_24h_v1_persisted_event_count": len(recent_persisted),
        "event_store_spec_version": EXPECTED_EVENT_STORE_SPEC,
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
        print(f"FAIL phase=event-store-v1 reason={reason}")
        return 1

    print("POLICY_CATALYST_EVENT_STORE_V1=" + json.dumps(summary, sort_keys=True))
    print("POLICY CATALYST EVENT STORE V1 VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required policy-catalyst event-store smoke configuration is missing")
        return 1
    return run_smoke(base_url, api_key, expected_sha)


if __name__ == "__main__":
    sys.exit(main())
