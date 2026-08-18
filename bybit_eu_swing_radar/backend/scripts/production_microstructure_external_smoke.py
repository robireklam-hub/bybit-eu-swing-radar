#!/usr/bin/env python3
"""Exact-SHA production smoke for the external microstructure recorder."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
from urllib.request import Request, urlopen

MAX_POLLS = 30
POLL_SECONDS = 4


def _get(base: str, path: str, api_key: str, timeout: float = 15.0) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "microstructure-external-smoke/1"}
    if api_key:
        headers["X-Radar-Key"] = api_key
    request = Request(base.rstrip("/") + path, headers=headers, method="GET")
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode())
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _safe_status(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "process_role": payload.get("process_role"),
        "external_service_healthy": payload.get("external_service_healthy"),
        "source_commit_sha": payload.get("source_commit_sha"),
        "service_id": payload.get("service_id"),
        "service_name": payload.get("service_name"),
        "heartbeat_at": payload.get("heartbeat_at"),
        "heartbeat_age_seconds": payload.get("heartbeat_age_seconds"),
        "running": payload.get("running"),
        "singleton_acquired": payload.get("singleton_acquired"),
        "connected": payload.get("connected"),
        "started_at": payload.get("started_at"),
        "last_message_at": payload.get("last_message_at"),
        "last_write_at": payload.get("last_write_at"),
        "messages": payload.get("messages"),
        "rows_written": payload.get("rows_written"),
        "reconnects": payload.get("reconnects"),
        "last_error": payload.get("last_error"),
        "symbols": payload.get("symbols"),
    }


def main() -> int:
    base = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base or not key or not expected_sha:
        print("FAIL missing production smoke configuration")
        return 1

    for attempt in range(MAX_POLLS):
        try:
            version = _get(base, "/version", "")
            if version.get("commit_sha") == expected_sha:
                break
        except Exception:
            pass
        if attempt + 1 == MAX_POLLS:
            print("FAIL exact production API SHA not serving")
            return 1
        time.sleep(POLL_SECONDS)

    status: dict[str, Any] | None = None
    for attempt in range(MAX_POLLS):
        try:
            candidate = _get(base, "/v1/research/microstructure/status", key)
        except Exception:
            candidate = {}
        print("EXTERNAL_RECORDER_STATUS=" + json.dumps(_safe_status(candidate), sort_keys=True))
        if (
            candidate.get("research_only") is True
            and candidate.get("live_strategy_mutated") is False
            and candidate.get("process_role") == "standalone"
            and candidate.get("external_service_healthy") is True
            and candidate.get("source_commit_sha") == expected_sha
            and candidate.get("running") is True
            and candidate.get("singleton_acquired") is True
            and candidate.get("connected") is True
            and float(candidate.get("heartbeat_age_seconds") or 9999) <= 30.0
            and int(candidate.get("messages") or 0) > 0
            and int(candidate.get("rows_written") or 0) > 0
        ):
            status = candidate
            break
        if attempt + 1 < MAX_POLLS:
            time.sleep(POLL_SECONDS)

    if status is None:
        print("FAIL external recorder is not healthy on expected SHA")
        return 1

    readiness = _get(base, "/v1/research/microstructure/readiness", key)
    safe_readiness = {
        "ready_for_forward_feature_analysis": readiness.get("ready_for_forward_feature_analysis"),
        "thresholds": readiness.get("thresholds"),
        "symbols": readiness.get("symbols"),
        "checked_at": readiness.get("checked_at"),
    }
    print("READINESS_STATUS=" + json.dumps(safe_readiness, sort_keys=True))
    if readiness.get("gate_version") != "microstructure-readiness-v1":
        print("FAIL unexpected readiness gate")
        return 1
    if readiness.get("promotion_allowed") is not False:
        print("FAIL promotion guard changed")
        return 1
    print("MICROSTRUCTURE EXTERNAL RECORDER VERIFIED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
