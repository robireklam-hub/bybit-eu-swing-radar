#!/usr/bin/env python3
"""Bounded commit gate followed by one read-only Flow freshness smoke."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


FINAL_PATHS = (
    ("FlowStatus", "/v1/day-trade/flow/status"),
    ("PENGUUSDC", "/v1/day-trade/flow/PENGUUSDC"),
    ("WIFUSDC", "/v1/day-trade/flow/WIFUSDC"),
    ("BONKUSDC", "/v1/day-trade/flow/BONKUSDC"),
)
TIMESTAMP_FIELDS = ("generated_at", "updated_at", "data_as_of")


class ImmediateHttpFailure(RuntimeError):
    pass


def parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp is missing or not a string")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def select_timestamp(payload: dict[str, Any]) -> tuple[str, datetime]:
    for field in TIMESTAMP_FIELDS:
        if field in payload and payload[field] not in (None, ""):
            return field, parse_timestamp(payload[field])
    raise ValueError("no canonical timestamp found")


def fetch_json(url: str, api_key: str, timeout: float) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "flow-freshness-smoke/2"}
    if api_key:
        headers["X-Radar-Key"] = api_key
    request = Request(url, method="GET", headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code in {401, 403, 429}:
            raise ImmediateHttpFailure(f"HTTP {exc.code}") from None
        raise
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def evaluate_status(payload: dict[str, Any]) -> tuple[datetime, list[str]]:
    _, reference_time = select_timestamp(payload)
    errors = []
    try:
        values = [int(payload[key]) for key in
                  ("processed", "good", "partial", "no_derivative_match")]
    except (KeyError, TypeError, ValueError):
        return reference_time, ["invalid FlowStatus counters"]
    processed, good, partial, no_match = values
    if min(values) < 0:
        errors.append("FlowStatus counters must be non-negative")
    if processed != good + partial + no_match:
        errors.append("processed != good + partial + no_derivative_match")
    return reference_time, errors


def evaluate_context(payload: dict[str, Any], reference_time: datetime) -> list[str]:
    _, payload_time = select_timestamp(payload)
    age = (reference_time - payload_time).total_seconds()
    errors = []
    if age < 0:
        errors.append("payload timestamp is later than FlowStatus reference time")
    if age > 300 and payload.get("data_quality") == "GOOD":
        errors.append("stale payload has data_quality=GOOD")
    if age > 300 and payload.get("coverage_status") == "GOOD":
        errors.append("stale payload has coverage_status=GOOD")
    return errors


def wait_for_commit_gate(
    base_url: str,
    api_key: str,
    expected_sha: str,
    *,
    fetch: Callable[[str, str, float], dict[str, Any]] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
    max_rounds: int = 20,
) -> str | None:
    baseline_batch_id = None
    for _ in range(max_rounds):
        try:
            if baseline_batch_id is None:
                version = fetch(f"{base_url.rstrip('/')}/version", "", 15)
                if version.get("commit_sha") == expected_sha:
                    baseline = fetch(
                        f"{base_url.rstrip('/')}/v1/day-trade/flow/status", api_key, 15
                    )
                    baseline_batch_id = baseline.get("flow_batch_id")
                    if not isinstance(baseline_batch_id, str) or not baseline_batch_id:
                        return None
            else:
                candidate = fetch(
                    f"{base_url.rstrip('/')}/v1/day-trade/flow/status", api_key, 15
                )
                candidate_batch_id = candidate.get("flow_batch_id")
                if (candidate.get("source_commit_sha") == expected_sha
                        and isinstance(candidate_batch_id, str)
                        and candidate_batch_id
                        and candidate_batch_id != baseline_batch_id):
                    return candidate_batch_id
        except ImmediateHttpFailure:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError):
            pass
        sleep(30)
    return None


def run_final_smoke(base_url, api_key, expected_sha, batch_id, *, fetch=fetch_json) -> int:
    responses = {}
    failures = []
    for name, path in FINAL_PATHS:
        try:
            responses[name] = fetch(f"{base_url.rstrip('/')}{path}", api_key, 15)
        except Exception:
            failures.append(f"{name}: request failed; credentials and response are redacted")
    if failures:
        return 1
    status = responses["FlowStatus"]
    try:
        reference_time, errors = evaluate_status(status)
        for name, _ in FINAL_PATHS:
            payload = responses[name]
            if payload.get("flow_batch_id") != batch_id:
                errors.append(f"{name}: flow_batch_id mismatch")
            if payload.get("source_commit_sha") != expected_sha:
                errors.append(f"{name}: source_commit_sha mismatch")
            if name != "FlowStatus":
                errors.extend(f"{name}: {error}" for error in evaluate_context(payload, reference_time))
    except (KeyError, ValueError):
        return 1
    if errors:
        return 1
    print("DEPLOYMENT VERIFIED, WORKER EXECUTION VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required smoke configuration is missing")
        return 1
    try:
        gate = wait_for_commit_gate(base_url, api_key, expected_sha)
    except ImmediateHttpFailure as exc:
        print(f"FAIL commit gate {exc}")
        return 1
    if gate is None:
        print("FAIL commit gate timed out")
        return 1
    return run_final_smoke(base_url, api_key, expected_sha, gate)


if __name__ == "__main__":
    sys.exit(main())
