#!/usr/bin/env python3
"""Exact-SHA production smoke for geopolitical news-attention shadow v1."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EXPECTED_SPEC = "geopolitical-risk-shadow-v1"
MAX_POLLS = 45
POLL_INTERVAL_SECONDS = 5
MIN_LIVE_TOPICS = 4


def fetch_json(url: str, api_key: str, timeout: float, method: str = "GET") -> dict[str, Any]:
    request = Request(
        url,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "bybit-eu-geopolitical-risk-shadow-smoke/1",
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


def validate_capture(payload: dict[str, Any], expected_sha: str) -> tuple[bool, str]:
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
    if payload.get("source_commit_sha") != expected_sha:
        return False, "capture_source_sha_mismatch"
    if payload.get("persisted") is not True:
        return False, "snapshot_not_persisted"
    if payload.get("data_quality") not in {"COMPLETE", "PARTIAL"}:
        return False, "no_usable_provider_coverage"
    coverage = payload.get("coverage") or {}
    if int(coverage.get("live_topic_count") or 0) < MIN_LIVE_TOPICS:
        return False, "insufficient_live_topics"
    topics = payload.get("topics") or {}
    statuses = coverage.get("source_status") or {}
    for name, status in statuses.items():
        if status.get("status") != "LIVE":
            continue
        row = topics.get(name) or {}
        if int((row.get("lookback_24h") or {}).get("bins") or 0) < 1:
            return False, f"live_topic_without_bins:{name}"
    if "risk_score" in payload or "trade_direction" in payload or "decision" in payload:
        return False, "forbidden_signal_field_present"
    return True, "ok"


def run_smoke(
    base_url: str,
    api_key: str,
    expected_sha: str,
    *,
    timeout: float = 45.0,
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
        spec_payload = _call(fetch, base_url, "/v1/research/geopolitical-risk/spec", api_key, timeout)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"FAIL phase=spec error_type={type(exc).__name__}")
        return 1
    if (
        spec_payload.get("version") != EXPECTED_SPEC
        or spec_payload.get("research_only") is not True
        or spec_payload.get("label_free") is not True
        or spec_payload.get("context_only") is not True
        or spec_payload.get("promotion_allowed") is not False
    ):
        print("FAIL phase=spec reason=research_contract_invalid")
        return 1

    try:
        capture = _call(fetch, base_url, "/v1/research/geopolitical-risk/capture", api_key, timeout, "POST")
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
            "coverage": capture.get("coverage"),
        }, sort_keys=True))
        return 1

    try:
        status = _call(fetch, base_url, "/v1/research/geopolitical-risk/status", api_key, timeout)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL phase=status error_type={type(exc).__name__}")
        return 1
    latest = status.get("latest") or {}
    if (
        int(status.get("snapshot_count") or 0) < 1
        or latest.get("captured_hour") != capture.get("captured_hour")
        or latest.get("source_commit_sha") != expected_sha
    ):
        print("FAIL phase=status reason=persisted_snapshot_not_visible")
        return 1

    compact_topics = {
        name: {
            "latest_bin_at": row.get("latest_bin_at"),
            "share_24h_pct": (row.get("lookback_24h") or {}).get("share_pct"),
            "recent_6h_share_pct": (row.get("recent_6h") or {}).get("share_pct"),
            "baseline_18h_share_pct": (row.get("baseline_18h") or {}).get("share_pct"),
            "recent_vs_baseline_share_ratio": row.get("recent_vs_baseline_share_ratio"),
        }
        for name, row in (capture.get("topics") or {}).items()
    }
    safe = {
        "captured_at": capture.get("captured_at"),
        "captured_hour": capture.get("captured_hour"),
        "source_commit_sha": capture.get("source_commit_sha"),
        "data_quality": capture.get("data_quality"),
        "coverage": capture.get("coverage"),
        "topics": compact_topics,
        "recent_attention_ranking": capture.get("recent_attention_ranking"),
    }
    print("GEOPOLITICAL_RISK_SHADOW=" + json.dumps(safe, sort_keys=True))
    print("GEOPOLITICAL RISK SHADOW V1 VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required geopolitical-risk smoke configuration is missing")
        return 1
    return run_smoke(base_url, api_key, expected_sha)


if __name__ == "__main__":
    sys.exit(main())
