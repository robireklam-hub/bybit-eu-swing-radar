#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def capture_due(status: dict[str, Any], *, now: datetime, min_age_seconds: float) -> tuple[bool, float | None]:
    if status.get("research_only") is not True:
        raise ValueError("forward status is not research-only")
    if status.get("live_strategy_mutated") is not False:
        raise ValueError("live strategy mutation guard changed")
    if status.get("promotion_allowed") is not False:
        raise ValueError("promotion guard changed")
    if min_age_seconds < 0:
        raise ValueError("min_age_seconds must be non-negative")

    raw = status.get("last_capture_at")
    if not raw:
        return True, None
    last_capture = _parse_timestamp(str(raw))
    age = (now.astimezone(timezone.utc) - last_capture).total_seconds()
    if age < -5:
        raise ValueError("last_capture_at is in the future")
    return age >= min_age_seconds, max(0.0, age)


def _get_status(base: str, api_key: str) -> dict[str, Any]:
    request = Request(
        base.rstrip("/") + "/v1/research/swing-liquidity/forward-status",
        headers={
            "Accept": "application/json",
            "User-Agent": "swing-liquidity-capture-due/1",
            "X-Radar-Key": api_key,
        },
        method="GET",
    )
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode())
    if not isinstance(payload, dict):
        raise ValueError("forward status JSON is not an object")
    return payload


def _write_output(name: str, value: str) -> None:
    output_path = os.getenv("GITHUB_OUTPUT", "").strip()
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def main() -> int:
    base = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "").strip()
    try:
        min_age_seconds = float(os.getenv("SWING_LIQUIDITY_MIN_AGE_SECONDS", "2700"))
    except ValueError:
        print("FAIL invalid SWING_LIQUIDITY_MIN_AGE_SECONDS")
        return 1
    if not base or not api_key:
        print("FAIL missing production API configuration")
        return 1

    try:
        status = _get_status(base, api_key)
        due, age = capture_due(status, now=datetime.now(timezone.utc), min_age_seconds=min_age_seconds)
    except Exception as exc:
        print(f"FAIL capture-due preflight: {type(exc).__name__}: {exc}")
        return 1

    _write_output("capture_due", "true" if due else "false")
    _write_output("last_capture_at", str(status.get("last_capture_at") or ""))
    _write_output("capture_age_seconds", "" if age is None else f"{age:.3f}")
    print(
        "SWING_LIQUIDITY_CAPTURE_DUE="
        + json.dumps(
            {
                "capture_due": due,
                "capture_age_seconds": age,
                "last_capture_at": status.get("last_capture_at"),
                "min_age_seconds": min_age_seconds,
                "capture_count": status.get("capture_count"),
                "candidate_observations": status.get("candidate_observations"),
                "research_only": status.get("research_only"),
                "live_strategy_mutated": status.get("live_strategy_mutated"),
                "promotion_allowed": status.get("promotion_allowed"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
