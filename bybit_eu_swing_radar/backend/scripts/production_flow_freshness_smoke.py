#!/usr/bin/env python3
"""Read-only production smoke test for cached Flow freshness."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PATHS = (
    ("FlowStatus", "/v1/day-trade/flow/status"),
    ("PENGUUSDC", "/v1/day-trade/flow/PENGUUSDC"),
    ("WIFUSDC", "/v1/day-trade/flow/WIFUSDC"),
    ("BONKUSDC", "/v1/day-trade/flow/BONKUSDC"),
)
TIMESTAMP_FIELDS = ("generated_at", "updated_at", "data_as_of")


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
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "bybit-eu-flow-freshness-smoke/1",
            "X-Radar-Key": api_key,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"HTTP {response.status}")
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def evaluate_status(payload: dict[str, Any]) -> tuple[datetime, list[str]]:
    _, reference_time = select_timestamp(payload)
    errors = []
    try:
        processed = int(payload["processed"])
        good = int(payload["good"])
        partial = int(payload["partial"])
        no_match = int(payload["no_derivative_match"])
    except (KeyError, TypeError, ValueError) as exc:
        return reference_time, [f"invalid FlowStatus counters: {exc}"]
    if min(processed, good, partial, no_match) < 0:
        errors.append("FlowStatus counters must be non-negative")
    if processed != good + partial + no_match:
        errors.append("processed != good + partial + no_derivative_match")
    return reference_time, errors


def evaluate_context(
    payload: dict[str, Any], reference_time: datetime
) -> tuple[str, datetime, float, list[str]]:
    field, payload_time = select_timestamp(payload)
    age = (reference_time - payload_time).total_seconds()
    errors = []
    if age < 0:
        errors.append("payload timestamp is later than FlowStatus reference time")
    if age > 300:
        if payload.get("data_quality") == "GOOD":
            errors.append("stale payload has data_quality=GOOD")
        if payload.get("coverage_status") == "GOOD":
            errors.append("stale payload has coverage_status=GOOD")
    return field, payload_time, age, errors


def run_smoke(
    base_url: str,
    api_key: str,
    *,
    timeout: float = 15.0,
    fetch: Callable[[str, str, float], dict[str, Any]] = fetch_json,
) -> int:
    failures = []
    responses: dict[str, dict[str, Any]] = {}
    for name, path in PATHS:
        try:
            responses[name] = fetch(f"{base_url.rstrip('/')}{path}", api_key, timeout)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError):
            failures.append(f"{name}: request failed; credentials and response are redacted")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1

    try:
        reference_field, reference_time = select_timestamp(responses["FlowStatus"])
        _, status_errors = evaluate_status(responses["FlowStatus"])
    except (KeyError, ValueError) as exc:
        print(f"FAIL FlowStatus: {exc}")
        return 1

    print(
        f"FlowStatus reference={reference_time.isoformat()} field={reference_field} "
        f"result={'FAIL' if status_errors else 'PASS'}"
    )
    failures.extend(f"FlowStatus: {error}" for error in status_errors)

    for name, _ in PATHS[1:]:
        payload = responses[name]
        try:
            field, payload_time, age, errors = evaluate_context(payload, reference_time)
            result = "FAIL" if errors else "PASS"
            print(
                f"{name} reference={reference_time.isoformat()} "
                f"payload_time={payload_time.isoformat()} field={field} "
                f"flow_age_seconds={age:.3f} "
                f"data_quality={payload.get('data_quality')} "
                f"coverage_status={payload.get('coverage_status')} "
                f"flow_batch_id={payload.get('flow_batch_id', 'n/a')} result={result}"
            )
            failures.extend(f"{name}: {error}" for error in errors)
        except ValueError as exc:
            failures.append(f"{name}: {exc}")
            print(f"{name} result=FAIL reason={exc}")

    for failure in failures:
        print(f"FAIL {failure}")
    return 1 if failures else 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    if not base_url or not api_key:
        print("FAIL required production smoke configuration is missing")
        return 1
    return run_smoke(base_url, api_key)


if __name__ == "__main__":
    sys.exit(main())
