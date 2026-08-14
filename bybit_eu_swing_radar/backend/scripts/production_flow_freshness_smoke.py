#!/usr/bin/env python3
"""Fail-closed production gate for the deployed API and its Flow worker."""

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
POLL_INTERVAL_SECONDS = 15
AUTH_OR_RATE_LIMIT_STATUSES = frozenset((401, 403, 429))
TRANSIENT_VERSION_STATUSES = frozenset((404, 502, 503, 504))


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
        "User-Agent": "bybit-eu-flow-freshness-smoke/2",
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


def valid_status_symbols(payload: dict[str, Any]) -> set[str] | None:
    value = payload.get("symbols")
    if not isinstance(value, list):
        return None
    symbols: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None
        symbols.add(item.strip().upper())
    if len(symbols) != len(value):
        return None
    return symbols


def evaluate_status(payload: dict[str, Any]) -> tuple[datetime, list[str]]:
    _, reference_time = select_timestamp(payload)
    errors = []
    try:
        counters = [int(payload[name]) for name in ("processed", "good", "partial", "no_derivative_match")]
    except (KeyError, TypeError, ValueError) as exc:
        return reference_time, [f"invalid FlowStatus counters: {exc}"]
    processed, good, partial, no_match = counters
    if min(counters) < 0:
        errors.append("FlowStatus counters must be non-negative")
    if processed != good + partial + no_match:
        errors.append("processed != good + partial + no_derivative_match")
    symbols = valid_status_symbols(payload)
    if symbols is None:
        errors.append("FlowStatus symbols must be a unique list of non-empty strings")
    elif processed != len(symbols):
        errors.append("processed != len(symbols)")
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


def _get(fetch: Callable[[str, str, float], dict[str, Any]], base_url: str, path: str, api_key: str, timeout: float) -> dict[str, Any]:
    return fetch(f"{base_url.rstrip('/')}{path}", api_key, timeout)


def _request_failure(phase: str, exc: BaseException) -> None:
    if isinstance(exc, HTTPError):
        print(f"FAIL phase={phase} http_status={exc.code}; credentials and response are redacted")
    else:
        print(f"FAIL phase={phase} error_type={type(exc).__name__}; credentials and response are redacted")


def run_smoke(base_url: str, api_key: str, expected_sha: str, *, timeout: float = 15.0,
              fetch: Callable[[str, str, float], dict[str, Any]] = fetch_json,
              sleep: Callable[[float], None] = time.sleep) -> int:
    """Verify API commit, worker execution, and final Flow freshness invariants."""
    deployment_verified = False
    for attempt in range(MAX_POLLS):
        try:
            version = _get(fetch, base_url, "/version", api_key, timeout)
        except HTTPError as exc:
            if exc.code in AUTH_OR_RATE_LIMIT_STATUSES:
                _request_failure("version_check", exc)
                return 1
            if exc.code not in TRANSIENT_VERSION_STATUSES:
                _request_failure("version_check", exc)
                return 1
        except (URLError, TimeoutError, OSError):
            pass
        except (ValueError, RuntimeError) as exc:
            _request_failure("version_check", exc)
            return 1
        else:
            if version.get("commit_sha") == expected_sha:
                deployment_verified = True
                break
        if attempt + 1 < MAX_POLLS:
            sleep(POLL_INTERVAL_SECONDS)

    if not deployment_verified:
        print("FAIL phase=version_check reason=expected_commit_not_deployed_before_timeout")
        return 1

    try:
        baseline = _get(fetch, base_url, STATUS_PATH, api_key, timeout)
    except HTTPError as exc:
        _request_failure("baseline_flow_status", exc)
        return 1
    except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        _request_failure("baseline_flow_status", exc)
        return 1

    baseline_batch_id = valid_batch_id(baseline)
    if baseline_batch_id is None:
        print("FAIL phase=baseline_flow_status reason=missing_or_invalid_flow_batch_id")
        return 1

    worker_verified = baseline.get("source_commit_sha") == expected_sha
    if not worker_verified:
        for attempt in range(MAX_POLLS):
            try:
                candidate = _get(fetch, base_url, STATUS_PATH, api_key, timeout)
            except HTTPError as exc:
                _request_failure("worker_check", exc)
                return 1
            except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
                _request_failure("worker_check", exc)
                return 1
            candidate_batch_id = valid_batch_id(candidate)
            if (candidate.get("source_commit_sha") == expected_sha and candidate_batch_id is not None
                    and candidate_batch_id != baseline_batch_id):
                worker_verified = True
                break
            if attempt + 1 < MAX_POLLS:
                sleep(POLL_INTERVAL_SECONDS)

    if not worker_verified:
        print("FAIL phase=worker_check reason=expected_worker_batch_not_observed_before_timeout")
        return 1

    try:
        responses = {name: _get(fetch, base_url, path, api_key, timeout) for name, path in PATHS}
    except HTTPError as exc:
        _request_failure("final_flow_smoke", exc)
        return 1
    except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        _request_failure("final_flow_smoke", exc)
        return 1

    failures = []
    status_payload = responses["FlowStatus"]
    final_batch_id = valid_batch_id(status_payload)
    current_symbols = valid_status_symbols(status_payload)
    try:
        reference_time, status_errors = evaluate_status(status_payload)
        failures.extend(status_errors)
    except ValueError as exc:
        failures.append(f"FlowStatus: {exc}")
        reference_time = None
    if status_payload.get("source_commit_sha") != expected_sha:
        failures.append("FlowStatus: source_commit_sha mismatch")
    if final_batch_id is None:
        failures.append("FlowStatus flow_batch_id is missing or invalid")

    for name, _ in PATHS:
        if name == "FlowStatus":
            continue
        payload = responses[name]
        if current_symbols is not None and name in current_symbols:
            if payload.get("source_commit_sha") != expected_sha:
                failures.append(f"{name}: source_commit_sha mismatch for current batch symbol")
            if valid_batch_id(payload) != final_batch_id:
                failures.append(f"{name}: flow_batch_id mismatch for current batch symbol")
        if reference_time is not None:
            try:
                failures.extend(f"{name}: {error}" for error in evaluate_context(payload, reference_time))
            except ValueError as exc:
                failures.append(f"{name}: {exc}")

    for failure in failures:
        print(f"FAIL {failure}")
    if failures:
        return 1
    print("DEPLOYMENT VERIFIED, WORKER EXECUTION VERIFIED.")
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
