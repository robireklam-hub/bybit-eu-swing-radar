#!/usr/bin/env python3
"""Exact-SHA production smoke for GDELT Event 2.0 geopolitical context v2."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EXPECTED_SPEC = "geopolitical-event-shadow-v2"
MAX_POLLS = 45
POLL_INTERVAL_SECONDS = 5
MAX_SOURCE_AGE_SECONDS = 3 * 60 * 60


def fetch_json(url: str, api_key: str, timeout: float, method: str = "GET") -> dict[str, Any]:
    request = Request(
        url,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "bybit-eu-geopolitical-event-v2-smoke/1",
            "X-Radar-Key": api_key,
        },
        data=b"{}" if method == "POST" else None,
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _call(fetch: Callable[..., dict[str, Any]], base_url: str, path: str, api_key: str, timeout: float, method: str = "GET") -> dict[str, Any]:
    return fetch(f"{base_url.rstrip('/')}{path}", api_key, timeout, method)


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def validate_capture(payload: dict[str, Any], expected_sha: str) -> tuple[bool, str]:
    if payload.get("research_only") is not True:
        return False, "research_only_not_true"
    if payload.get("label_free") is not True:
        return False, "label_free_not_true"
    if payload.get("context_only") is not True:
        return False, "context_only_not_true"
    if payload.get("promotion_allowed") is not False:
        return False, "promotion_allowed_not_false"
    if payload.get("live_strategy_mutated") is not False:
        return False, "live_strategy_mutated_not_false"
    if (payload.get("spec") or {}).get("version") != EXPECTED_SPEC:
        return False, "unexpected_spec_version"
    if (payload.get("spec") or {}).get("source_family") != "STATIC_GDELT_EVENT_EXPORT":
        return False, "unexpected_source_family"
    if payload.get("source_commit_sha") != expected_sha:
        return False, "capture_source_sha_mismatch"
    if payload.get("persisted") is not True:
        return False, "snapshot_not_persisted"
    if payload.get("data_quality") not in {"COMPLETE", "PARTIAL"}:
        return False, "no_usable_event_data"

    source = payload.get("source_file") or {}
    source_timestamp = _parse_dt(source.get("source_file_timestamp"))
    if source_timestamp is None:
        return False, "source_file_timestamp_missing"
    age = (datetime.now(timezone.utc) - source_timestamp).total_seconds()
    if age < -300 or age > MAX_SOURCE_AGE_SECONDS:
        return False, "source_file_not_fresh"
    if source.get("freshness") != "FRESH":
        return False, "source_freshness_not_fresh"
    filename = str(source.get("source_filename") or "")
    if not filename.endswith(".export.CSV.zip"):
        return False, "not_event_export_file"
    if not str(source.get("download_url") or "").endswith(".export.CSV.zip"):
        return False, "download_url_not_event_export"
    if not source.get("actual_md5"):
        return False, "source_md5_missing"

    coverage = payload.get("coverage") or {}
    total_rows = int(coverage.get("total_rows") or 0)
    valid_rows = int(coverage.get("valid_rows") or 0)
    invalid_rows = int(coverage.get("invalid_rows") or 0)
    if total_rows < 1 or valid_rows < 1 or total_rows != valid_rows + invalid_rows:
        return False, "invalid_row_coverage"

    context = payload.get("event_context") or {}
    all_events = context.get("all_events") or {}
    material = context.get("material_conflict") or {}
    if int(all_events.get("event_count") or 0) != valid_rows:
        return False, "event_count_mismatch"
    if int(all_events.get("root_event_count") or 0) > valid_rows:
        return False, "root_event_count_invalid"
    if int(material.get("event_count") or 0) > valid_rows:
        return False, "material_conflict_count_invalid"
    quad_counts = context.get("quad_class_counts") or {}
    if sum(int(value or 0) for value in quad_counts.values()) != valid_rows:
        return False, "quad_class_distribution_mismatch"
    if "risk_score" in payload or "trade_direction" in payload or "decision" in payload:
        return False, "forbidden_signal_field_present"
    return True, "ok"


def run_smoke(
    base_url: str,
    api_key: str,
    expected_sha: str,
    *,
    timeout: float = 90.0,
    fetch: Callable[..., dict[str, Any]] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    deployed = False
    for attempt in range(MAX_POLLS):
        try:
            version = _call(fetch, base_url, "/version", api_key, 20.0)
        except (HTTPError, URLError, TimeoutError, OSError):
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
        spec_payload = _call(fetch, base_url, "/v1/research/geopolitical-event-v2/spec", api_key, 30.0)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"FAIL phase=spec error_type={type(exc).__name__}")
        return 1
    if (
        spec_payload.get("version") != EXPECTED_SPEC
        or spec_payload.get("research_only") is not True
        or spec_payload.get("historical_backfill_allowed") is not False
    ):
        print("FAIL phase=spec reason=research_contract_invalid")
        return 1

    try:
        capture = _call(fetch, base_url, "/v1/research/geopolitical-event-v2/capture", api_key, timeout, "POST")
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
            "source_file": capture.get("source_file"),
            "coverage": capture.get("coverage"),
            "event_context": capture.get("event_context"),
        }, sort_keys=True))
        return 1

    try:
        status = _call(fetch, base_url, "/v1/research/geopolitical-event-v2/status", api_key, 30.0)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL phase=status error_type={type(exc).__name__}")
        return 1
    latest = status.get("latest") or {}
    source_timestamp = (capture.get("source_file") or {}).get("source_file_timestamp")
    if (
        int(status.get("snapshot_count") or 0) < 1
        or latest.get("source_file_timestamp") != source_timestamp
        or latest.get("source_commit_sha") != expected_sha
    ):
        print("FAIL phase=status reason=persisted_snapshot_not_visible")
        return 1

    context = capture.get("event_context") or {}
    safe = {
        "captured_at": capture.get("captured_at"),
        "source_commit_sha": capture.get("source_commit_sha"),
        "data_quality": capture.get("data_quality"),
        "source_file": capture.get("source_file"),
        "coverage": capture.get("coverage"),
        "quad_class_counts": context.get("quad_class_counts"),
        "material_conflict": context.get("material_conflict"),
        "all_events": context.get("all_events"),
        "snapshot_count": status.get("snapshot_count"),
        "prospective_start_at": status.get("prospective_start_at"),
    }
    print("GEOPOLITICAL_EVENT_V2=" + json.dumps(safe, sort_keys=True))
    print("GEOPOLITICAL EVENT SHADOW V2 VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required geopolitical-event-v2 smoke configuration is missing")
        return 1
    return run_smoke(base_url, api_key, expected_sha)


if __name__ == "__main__":
    sys.exit(main())
