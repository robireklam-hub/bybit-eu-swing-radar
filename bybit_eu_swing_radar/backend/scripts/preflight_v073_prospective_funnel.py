"""Bounded read-only preflight for the shared standalone prospective research sidecar.

Trusted main CI uses this before requesting any explicit Railway deployment. The
preflight gives normal auto-deploy + cron capture a short settling window and
only falls back to provisioning when exact-main evidence for every recorder does
not appear.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.production_v073_prospective_funnel_smoke import (
    validate_barrier_observer_status,
    validate_barrier_parent_status,
    validate_standalone_status,
)

PREFLIGHT_POLL_SECONDS = 10
PREFLIGHT_MAX_WAIT_SECONDS = 300


def validate_externalized_marker(payload: dict[str, Any]) -> list[str]:
    marker = payload.get("prospective_funnel") or {}
    errors: list[str] = []
    expected = {
        "status": "EXTERNALIZED",
        "enabled": False,
        "reason": "STANDALONE_RECORDER_OWNS_CAPTURE",
        "execution_mode": "STANDALONE_RAILWAY_CRON",
    }
    for key, value in expected.items():
        if marker.get(key) != value:
            errors.append(f"prospective_funnel.{key} mismatch")
    return errors


def evaluate_preflight(
    *,
    version: dict[str, Any],
    live_status: dict[str, Any],
    prospective_status: dict[str, Any],
    barrier_parent_status: dict[str, Any],
    barrier_observer_status: dict[str, Any],
    expected_sha: str,
) -> dict[str, Any]:
    errors: list[str] = []
    if version.get("commit_sha") != expected_sha:
        errors.append("production API SHA mismatch")
    errors.extend(validate_externalized_marker(live_status))
    live_strategy_version = live_status.get("strategy_version")
    if not isinstance(live_strategy_version, str) or not live_strategy_version:
        errors.append("live strategy_version missing")
        live_strategy_version = ""
    prospective = validate_standalone_status(prospective_status, expected_sha)
    parent = validate_barrier_parent_status(
        barrier_parent_status, expected_sha, live_strategy_version
    )
    observer = validate_barrier_observer_status(
        barrier_observer_status, expected_sha, live_strategy_version
    )
    for evidence in (prospective, parent, observer):
        errors.extend(str(item) for item in evidence.get("errors") or [])
    return {
        "ok": not errors,
        "errors": errors,
        "api_commit_sha": version.get("commit_sha"),
        "live_strategy_version": live_strategy_version or None,
        "prospective": prospective,
        "barrier_parent": parent,
        "barrier_observer": observer,
    }


def wait_for_preflight(
    fetch: Callable[[str, bool], dict[str, Any]],
    *,
    expected_sha: str,
    max_attempts: int,
    sleep_seconds: float,
) -> dict[str, Any]:
    """Wait for existing exact-main production evidence without mutating Railway."""
    last: dict[str, Any] = {"ok": False, "errors": ["preflight not attempted"]}
    for attempt in range(max(1, max_attempts)):
        try:
            last = evaluate_preflight(
                version=fetch("/version", False),
                live_status=fetch("/v1/day-trade/status", True),
                prospective_status=fetch("/v1/day-trade/research/prospective-funnel/status", True),
                barrier_parent_status=fetch(
                    "/v1/day-trade/research/barrier-clear-rearm/parent-status", True
                ),
                barrier_observer_status=fetch(
                    "/v1/day-trade/research/barrier-clear-rearm/observer-status", True
                ),
                expected_sha=expected_sha,
            )
        except Exception as exc:
            last = {"ok": False, "errors": ["fetch error: " + type(exc).__name__]}
        if attempt % 3 == 0 or last.get("ok"):
            print(
                "PROSPECTIVE_PREFLIGHT_PROGRESS="
                + json.dumps({"attempt": attempt + 1, **last}, sort_keys=True, default=str),
                flush=True,
            )
        if last.get("ok"):
            return last
        if attempt < max_attempts - 1:
            time.sleep(sleep_seconds)
    return last


def main() -> int:
    base = os.environ["PRODUCTION_RADAR_API_BASE_URL"].rstrip("/")
    key = os.environ["PRODUCTION_RADAR_API_KEY"]
    expected_sha = os.environ["EXPECTED_API_SHA"]

    def get(path: str, auth: bool = True) -> dict[str, Any]:
        headers = {"Accept": "application/json", "User-Agent": "standalone-research-preflight/3"}
        if auth:
            headers["X-Radar-Key"] = key
        with urlopen(Request(base + path, headers=headers), timeout=20) as response:
            payload = json.loads(response.read().decode())
        if not isinstance(payload, dict):
            raise RuntimeError("non-object payload: " + path)
        return payload

    max_attempts = max(1, PREFLIGHT_MAX_WAIT_SECONDS // PREFLIGHT_POLL_SECONDS)
    result = wait_for_preflight(
        get,
        expected_sha=expected_sha,
        max_attempts=max_attempts,
        sleep_seconds=PREFLIGHT_POLL_SECONDS,
    )
    print("PROSPECTIVE_PREFLIGHT=" + json.dumps(result, sort_keys=True, default=str), flush=True)
    if result.get("ok"):
        print("SHARED PROSPECTIVE RESEARCH EXACT-MAIN PREFLIGHT VERIFIED.", flush=True)
        return 0
    print("SHARED PROSPECTIVE RESEARCH PREFLIGHT EXHAUSTED; FALLBACK REQUIRED.", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
