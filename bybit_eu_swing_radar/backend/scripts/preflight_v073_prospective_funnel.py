"""Single-shot production preflight for the standalone v0.7.3 prospective funnel.

This is intentionally read-only and non-polling. It lets trusted main CI skip a
redundant Railway deploy when the auto-deployed standalone recorder already
publishes a fresh exact-main capture.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.request import Request, urlopen

from scripts.production_v073_prospective_funnel_smoke import validate_standalone_status


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
    expected_sha: str,
) -> dict[str, Any]:
    errors: list[str] = []
    if version.get("commit_sha") != expected_sha:
        errors.append("production API SHA mismatch")
    errors.extend(validate_externalized_marker(live_status))
    evidence = validate_standalone_status(prospective_status, expected_sha)
    errors.extend(str(item) for item in evidence.get("errors") or [])
    return {
        "ok": not errors,
        "errors": errors,
        "api_commit_sha": version.get("commit_sha"),
        "prospective": evidence,
    }


def main() -> int:
    base = os.environ["PRODUCTION_RADAR_API_BASE_URL"].rstrip("/")
    key = os.environ["PRODUCTION_RADAR_API_KEY"]
    expected_sha = os.environ["EXPECTED_API_SHA"]

    def get(path: str, *, auth: bool = True) -> dict[str, Any]:
        headers = {"Accept": "application/json", "User-Agent": "standalone-funnel-preflight/1"}
        if auth:
            headers["X-Radar-Key"] = key
        with urlopen(Request(base + path, headers=headers), timeout=20) as response:
            payload = json.loads(response.read().decode())
        if not isinstance(payload, dict):
            raise RuntimeError("non-object payload: " + path)
        return payload

    try:
        result = evaluate_preflight(
            version=get("/version", auth=False),
            live_status=get("/v1/day-trade/status"),
            prospective_status=get("/v1/day-trade/research/prospective-funnel/status"),
            expected_sha=expected_sha,
        )
    except Exception as exc:
        print("PROSPECTIVE_PREFLIGHT_ERROR=" + type(exc).__name__, flush=True)
        return 1

    print("PROSPECTIVE_PREFLIGHT=" + json.dumps(result, sort_keys=True, default=str), flush=True)
    if result["ok"]:
        print("V0.7.3 PROSPECTIVE FUNNEL EXACT-MAIN PREFLIGHT VERIFIED.", flush=True)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
