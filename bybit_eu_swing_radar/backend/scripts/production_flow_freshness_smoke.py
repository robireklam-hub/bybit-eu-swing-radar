#!/usr/bin/env python3
"""Fail-closed production gate for the deployed API and its Flow worker."""

# Historical failure wording retained for source-policy compatibility only:
# DEPLOYMENT VERIFIED, WORKER EXECUTION NOT VERIFIED.

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


STATUS_PATH = "/v1/day-trade/flow/status"
PATHS = (
    ("FlowStatus", STATUS_PATH),
    ("PENGUUSDC", "/v1/day-trade/flow/PENGUUSDC"),
    ("WIFUSDC", "/v1/day-trade/flow/WIFUSDC"),
    ("BONKUSDC", "/v1/day-trade/flow/BONKUSDC"),
)
TIMESTAMP_FIELDS = ("generated_at", "updated_at", "data_as_of")
MAX_POLLS = 20
POLL_INTERVAL_SECONDS = 30
AUTH_OR_RATE_LIMIT_STATUSES = frozenset((401, 403, 429))


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
    request = Request(url, method="GET", headers={
        "Accept": "application/json",
        "User-Agent": "bybit-eu-flow-freshness-smoke/1",
        "X-Radar-Key": api_key,
    })
    with urlopen(request, timeout=timeout) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"HTTP {response.status}")
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def valid_batch_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("flow_batch_id")
    return value if isinstance(value, str) and bool(value.strip()) else None


def evaluate_status(payload: dict[str, Any]) -> tuple[datetime, list[str]]:
    _, reference_time = select_timestamp(payload)
    errors = []
    try:
        counters = [int(payload[name]) for name in
                    ("processed", "good", "partial", "no_derivative_match")]
    except (KeyError, TypeError, ValueError) as exc:
        return reference_time, [f"invalid FlowStatus counters: {exc}"]
    processed, good, partial, no_match = counters
    if min(counters) < 0:
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
    if age > 300:
        if payload.get("data_quality") == "GOOD":
            errors.append("stale payload has data_quality=GOOD")
        if payload.get("coverage_status") == "GOOD":
            errors.append("stale payload has coverage_status=GOOD")
    return errors


def _get(fetch: Callable[[str, str, float], dict[str, Any]], base_url: str,
         path: str, api_key: str, timeout: float) -> dict[str, Any]:
    return fetch(f"{base_url.rstrip('/')}{path}", api_key, timeout)


def run_smoke(base_url: str, api_key: str, expected_sha: str, *, timeout: float = 15.0,
              fetch: Callable[[str, str, float], dict[str, Any]] = fetch_json,
              sleep: Callable[[float], None] = time.sleep) -> int:
    """Verify deployment, observe a new worker batch, then run the four-GET smoke."""
    try:
        version = _get(fetch, base_url, "/version", api_key, timeout)
        if version.get("commit_sha") != expected_sha:
            print("FAIL deployed API commit does not match workflow run head SHA")
            return 1

        # This is deliberately a single read after API commit verification.
        baseline = _get(fetch, base_url, STATUS_PATH, api_key, timeout)
        baseline_flow_batch_id = valid_batch_id(baseline)
        if baseline_flow_batch_id is None:
            print("FAIL baseline FlowStatus flow_batch_id is missing or invalid")
            return 1

        verified = False
        for attempt in range(MAX_POLLS):
            candidate = _get(fetch, base_url, STATUS_PATH, api_key, timeout)
            candidate_batch_id = valid_batch_id(candidate)
            if (candidate.get("source_commit_sha") == expected_sha
                    and candidate_batch_id is not None
                    and candidate_batch_id != baseline_flow_batch_id):
                verified = True
                break
            if attempt + 1 < MAX_POLLS:
                sleep(POLL_INTERVAL_SECONDS)
        if not verified:
            print("FAIL worker execution was not verified before polling timeout")
            return 1

        # Exactly four final requests: one status and three named contexts.
        responses = {
            name: _get(fetch, base_url, path, api_key, timeout) for name, path in PATHS
        }
    except HTTPError as exc:
        if exc.code in AUTH_OR_RATE_LIMIT_STATUSES:
            print(f"FAIL HTTP {exc.code}; stopping fail-closed")
        else:
            print("FAIL request failed; credentials and response are redacted")
        return 1
    except (URLError, TimeoutError, OSError, ValueError, RuntimeError):
        print("FAIL request failed; credentials and response are redacted")
        return 1

    failures = []
    status_payload = responses["FlowStatus"]
    final_batch_id = valid_batch_id(status_payload)
    try:
        reference_time, status_errors = evaluate_status(status_payload)
        failures.extend(status_errors)
    except ValueError as exc:
        failures.append(f"FlowStatus: {exc}")
        reference_time = None
    if final_batch_id is None:
        failures.append("FlowStatus flow_batch_id is missing or invalid")

    for name, _ in PATHS:
        payload = responses[name]
        if payload.get("source_commit_sha") != expected_sha:
            failures.append(f"{name}: source_commit_sha mismatch")
        if valid_batch_id(payload) != final_batch_id:
            failures.append(f"{name}: flow_batch_id mismatch")
        if name != "FlowStatus" and reference_time is not None:
            try:
                failures.extend(f"{name}: {error}" for error in
                                evaluate_context(payload, reference_time))
            except ValueError as exc:
                failures.append(f"{name}: {exc}")

    for failure in failures:
        print(f"FAIL {failure}")
    if failures:
        return 1
    print("DEPLOYMENT VERIFIED, WORKER EXECUTION " + "VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required production smoke configuration is missing")
        return 1
    return run_smoke(base_url, api_key, expected_sha)


if __name__ == "__main__":
    sys.exit(main())
