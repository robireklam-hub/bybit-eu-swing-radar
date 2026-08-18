#!/usr/bin/env python3
"""Exact-SHA production smoke for Event & Tokenomics Intelligence v1."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EXPECTED_SPEC = "event-tokenomics-shadow-v1"
MAX_POLLS = 30
POLL_INTERVAL_SECONDS = 5


def fetch_json(url: str, api_key: str, timeout: float, method: str = "GET") -> dict[str, Any]:
    request = Request(
        url,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "bybit-eu-event-tokenomics-shadow-smoke/1",
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


def validate_capture(payload: dict[str, Any]) -> tuple[bool, str]:
    if payload.get("research_only") is not True:
        return False, "research_only_not_true"
    if payload.get("label_free") is not True:
        return False, "label_free_not_true"
    if payload.get("context_only") is not True:
        return False, "context_only_not_true"
    if payload.get("live_strategy_mutated") is not False:
        return False, "live_strategy_mutated_not_false"
    if payload.get("promotion_allowed") is not False:
        return False, "promotion_allowed_not_false"
    if (payload.get("spec") or {}).get("version") != EXPECTED_SPEC:
        return False, "unexpected_spec_version"
    if payload.get("persisted") is not True:
        return False, "snapshot_not_persisted"
    coverage = payload.get("coverage") or {}
    statuses = coverage.get("source_status") or {}
    if (statuses.get("fomc_schedule") or {}).get("status") != "LIVE":
        return False, "fomc_schedule_not_live"
    if not any((statuses.get(name) or {}).get("status") in {"LIVE", "PARTIAL"} for name in ("bls_macro", "bybit_announcements")):
        return False, "no_live_network_keyless_source"
    if int(payload.get("event_count") or 0) < 1:
        return False, "no_active_events"
    tracked = payload.get("tracked_symbols") or []
    if "BTCUSDC" not in tracked or any(not str(symbol).endswith("USDC") for symbol in tracked):
        return False, "invalid_usdc_universe"
    return True, "ok"


def run_smoke(
    base_url: str,
    api_key: str,
    expected_sha: str,
    *,
    timeout: float = 35.0,
    fetch: Callable[..., dict[str, Any]] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    deployed = False
    for attempt in range(MAX_POLLS):
        try:
            version = _call(fetch, base_url, "/version", api_key, timeout)
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
        capture = _call(fetch, base_url, "/v1/research/event-tokenomics/capture", api_key, timeout, "POST")
    except HTTPError as exc:
        print(f"FAIL phase=capture http_status={exc.code}")
        return 1
    except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL phase=capture error_type={type(exc).__name__}")
        return 1

    ok, reason = validate_capture(capture)
    if not ok:
        print(f"FAIL phase=capture reason={reason}")
        print("CAPTURE_SAFE=" + json.dumps({
            "event_count": capture.get("event_count"),
            "coverage": capture.get("coverage"),
        }, sort_keys=True))
        return 1

    try:
        status = _call(fetch, base_url, "/v1/research/event-tokenomics/status", api_key, timeout)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL phase=status error_type={type(exc).__name__}")
        return 1
    latest = status.get("latest") or {}
    if status.get("research_only") is not True or status.get("promotion_allowed") is not False:
        print("FAIL phase=status reason=research_contract_invalid")
        return 1
    # Snapshots are intentionally idempotent per UTC hour. A concurrent scheduled
    # capture may overwrite captured_at inside the same row between POST /capture
    # and GET /status, so exact microsecond equality is not a valid persistence
    # contract. The persisted row must instead be the same captured hour and exact
    # production SHA.
    if (
        int(status.get("snapshot_count") or 0) < 1
        or latest.get("captured_hour") != capture.get("captured_hour")
        or latest.get("source_commit_sha") != expected_sha
    ):
        print("FAIL phase=status reason=persisted_snapshot_not_visible")
        return 1

    compact_events = [
        {
            "event_type": event.get("event_type"),
            "title": event.get("title"),
            "event_at": event.get("event_at"),
            "display_date": event.get("display_date"),
            "severity": event.get("severity"),
            "symbols": event.get("symbols"),
            "window": event.get("window"),
            "source": (event.get("source") or {}).get("name"),
            "tokenomics": event.get("tokenomics"),
        }
        for event in (capture.get("events") or [])[:20]
    ]
    safe = {
        "captured_at": capture.get("captured_at"),
        "captured_hour": capture.get("captured_hour"),
        "source_commit_sha": capture.get("source_commit_sha"),
        "event_count": capture.get("event_count"),
        "tracked_symbol_count": (capture.get("coverage") or {}).get("tracked_symbol_count"),
        "source_status": (capture.get("coverage") or {}).get("source_status"),
        "event_type_counts": capture.get("event_type_counts"),
        "severity_counts": capture.get("severity_counts"),
        "window_counts": capture.get("window_counts"),
        "events": compact_events,
    }
    print("EVENT_TOKENOMICS_SHADOW=" + json.dumps(safe, sort_keys=True))
    print("EVENT TOKENOMICS SHADOW V1 VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required event-tokenomics smoke configuration is missing")
        return 1
    return run_smoke(base_url, api_key, expected_sha)


if __name__ == "__main__":
    sys.exit(main())
