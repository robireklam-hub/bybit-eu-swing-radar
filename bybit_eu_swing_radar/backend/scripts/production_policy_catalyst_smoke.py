#!/usr/bin/env python3
"""Exact-SHA production smoke for the primary-source policy catalyst feed."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EXPECTED_SPEC = "policy-catalyst-feed-v1"
MAX_POLLS = 45
POLL_INTERVAL_SECONDS = 5


def fetch_json(url: str, api_key: str, timeout: float, method: str = "GET") -> dict[str, Any]:
    request = Request(
        url,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "bybit-eu-policy-catalyst-smoke/1",
            "X-Radar-Key": api_key,
        },
        data=b"{}" if method == "POST" else None,
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _call(
    fetch: Callable[..., dict[str, Any]],
    base_url: str,
    path: str,
    api_key: str,
    timeout: float,
    method: str = "GET",
) -> dict[str, Any]:
    return fetch(f"{base_url.rstrip('/')}{path}", api_key, timeout, method)


def validate_capture(payload: dict[str, Any], expected_sha: str) -> tuple[bool, str]:
    if payload.get("research_only") is not True:
        return False, "research_only_not_true"
    if payload.get("label_free") is not True:
        return False, "label_free_not_true"
    if payload.get("context_only") is not True or payload.get("hard_gate") is not False:
        return False, "context_only_contract_invalid"
    if payload.get("live_strategy_mutated") is not False:
        return False, "live_strategy_mutated_not_false"
    if (payload.get("spec") or {}).get("version") != EXPECTED_SPEC:
        return False, "unexpected_spec_version"
    if payload.get("source_commit_sha") != expected_sha:
        return False, "capture_source_sha_mismatch"
    if payload.get("persisted") is not True:
        return False, "snapshot_not_persisted"
    if payload.get("data_quality") not in {"COMPLETE", "PARTIAL"}:
        return False, "no_usable_primary_source_capture"
    if payload.get("causal_attribution") != "UNCONFIRMED_CONTEXT_ONLY":
        return False, "causal_attribution_contract_invalid"

    coverage = payload.get("coverage") or {}
    attempted = int(coverage.get("attempted_source_count") or 0)
    ok_count = int(coverage.get("ok_source_count") or 0)
    failed = int(coverage.get("failed_source_count") or 0)
    if attempted < 1:
        return False, "no_primary_sources_attempted"
    if ok_count < 1:
        return False, "all_primary_sources_unavailable"
    if ok_count + failed != attempted:
        return False, "source_coverage_accounting_invalid"
    source_results = payload.get("source_results") or []
    if len(source_results) != attempted:
        return False, "source_result_count_mismatch"
    for source in source_results:
        if source.get("status") not in {"OK", "ERROR"}:
            return False, "source_failure_not_explicit"

    for event in payload.get("events") or []:
        if event.get("context_only") is not True or event.get("hard_gate") is not False:
            return False, "event_context_contract_invalid"
        if event.get("trade_direction") is not None:
            return False, "trade_direction_present"
        if event.get("execution_mutation") is not False:
            return False, "execution_mutation_present"
        if not event.get("first_seen_at"):
            return False, "first_seen_at_missing_after_persistence"
    return True, "ok"


def run_smoke(
    base_url: str,
    api_key: str,
    expected_sha: str,
    *,
    fetch: Callable[..., dict[str, Any]] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    deployed = False
    for attempt in range(MAX_POLLS):
        try:
            version = _call(fetch, base_url, "/version", api_key, 20.0)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            version = {}
        if version.get("commit_sha") == expected_sha:
            deployed = True
            break
        if attempt + 1 < MAX_POLLS:
            sleep(POLL_INTERVAL_SECONDS)
    if not deployed:
        print("FAIL phase=version reason=expected_commit_not_deployed")
        return 1

    try:
        spec_payload = _call(fetch, base_url, "/v1/research/policy-catalyst/spec", api_key, 30.0)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"FAIL phase=spec error_type={type(exc).__name__}")
        return 1
    if (
        spec_payload.get("version") != EXPECTED_SPEC
        or spec_payload.get("context_only") is not True
        or spec_payload.get("hard_gate") is not False
        or spec_payload.get("trade_direction") is not None
    ):
        print("FAIL phase=spec reason=policy_contract_invalid")
        return 1

    try:
        capture = _call(fetch, base_url, "/v1/research/policy-catalyst/capture", api_key, 90.0, "POST")
    except HTTPError as exc:
        print(f"FAIL phase=capture http_status={exc.code}")
        return 1
    except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL phase=capture error_type={type(exc).__name__}")
        return 1

    ok, reason = validate_capture(capture, expected_sha)
    if not ok:
        print(f"FAIL phase=capture reason={reason}")
        print("CAPTURE_SAFE=" + json.dumps({
            "data_quality": capture.get("data_quality"),
            "source_commit_sha": capture.get("source_commit_sha"),
            "coverage": capture.get("coverage"),
            "source_results": capture.get("source_results"),
        }, sort_keys=True))
        return 1

    try:
        status = _call(fetch, base_url, "/v1/research/policy-catalyst/status", api_key, 30.0)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"FAIL phase=status error_type={type(exc).__name__}")
        return 1
    if status.get("freshness") != "FRESH":
        print("FAIL phase=status reason=latest_capture_not_fresh")
        return 1
    latest = status.get("latest_capture") or {}
    if latest.get("source_commit_sha") != expected_sha:
        print("FAIL phase=status reason=latest_capture_sha_mismatch")
        return 1

    try:
        market = _call(fetch, base_url, "/v1/market-regime", api_key, 30.0)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"FAIL phase=market-context error_type={type(exc).__name__}")
        return 1
    alerts = market.get("market_context_alerts") or {}
    policy = alerts.get("policy_catalyst") or {}
    if alerts.get("context_only") is not True or alerts.get("hard_gate") is not False:
        print("FAIL phase=market-context reason=alert_mutation_contract_invalid")
        return 1
    if policy.get("state") not in {"ACTIVE", "NORMAL", "STALE"}:
        print("FAIL phase=market-context reason=policy_context_not_visible")
        return 1
    if policy.get("context_only") is not True or policy.get("hard_gate") is not False:
        print("FAIL phase=market-context reason=policy_mutation_contract_invalid")
        return 1

    safe = {
        "source_commit_sha": capture.get("source_commit_sha"),
        "data_quality": capture.get("data_quality"),
        "coverage": capture.get("coverage"),
        "freshness": status.get("freshness"),
        "recent_24h_event_count": len(status.get("recent_24h_events") or []),
        "market_policy_state": policy.get("state"),
        "market_policy_event_count": len(policy.get("recent_events") or []),
    }
    print("POLICY_CATALYST_V1=" + json.dumps(safe, sort_keys=True))
    print("POLICY CATALYST FEED V1 VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required policy-catalyst smoke configuration is missing")
        return 1
    return run_smoke(base_url, api_key, expected_sha)


if __name__ == "__main__":
    sys.exit(main())
