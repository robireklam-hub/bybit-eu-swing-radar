#!/usr/bin/env python3
"""Exact-SHA production smoke for Relative Strength / Rotation Shadow v1."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EXPECTED_SPEC = "relative-strength-shadow-v1"
MAX_POLLS = 30
POLL_INTERVAL_SECONDS = 5
VALID_STATES = {"LEADER", "OUTPERFORMER", "NEUTRAL", "UNDERPERFORMER", "LAGGARD"}
VALID_ROTATION = {"ACCELERATING", "STABLE", "DECELERATING"}


def fetch_json(
    url: str, api_key: str, timeout: float, method: str = "GET"
) -> dict[str, Any]:
    request = Request(
        url,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Radar-Key": api_key,
        },
        data=b"{}" if method == "POST" else None,
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _call(
    fetch: Callable[..., dict[str, Any]],
    base: str,
    path: str,
    key: str,
    timeout: float,
    method: str = "GET",
) -> dict[str, Any]:
    return fetch(f"{base.rstrip('/')}{path}", key, timeout, method)


def validate_capture(payload: dict[str, Any], expected_sha: str) -> tuple[bool, str]:
    if payload.get("research_only") is not True or payload.get("label_free") is not True:
        return False, "research_contract_invalid"
    if payload.get("context_only") is not True or payload.get("live_strategy_mutated") is not False:
        return False, "mutation_contract_invalid"
    if payload.get("promotion_allowed") is not False:
        return False, "promotion_contract_invalid"
    if (payload.get("spec") or {}).get("version") != EXPECTED_SPEC:
        return False, "unexpected_spec"
    if payload.get("persisted") is not True:
        return False, "not_persisted"
    if payload.get("source_commit_sha") != expected_sha:
        return False, "wrong_source_commit"
    if payload.get("sector_rotation_available") is not False:
        return False, "unsourced_sector_rotation_enabled"
    if payload.get("sector_metadata_status") != "NOT_INCLUDED_UNSOURCED":
        return False, "sector_metadata_contract_invalid"

    symbols = payload.get("symbols") or []
    universe_size = int(payload.get("universe_size") or 0)
    requested = payload.get("requested_symbols") or []
    analyzed = payload.get("analyzed_symbols") or []
    if not (12 <= universe_size <= 20):
        return False, "universe_size_invalid"
    if len(symbols) != universe_size or len(analyzed) != universe_size:
        return False, "analysis_count_mismatch"
    if len(requested) > 20 or "BTCUSDC" not in analyzed:
        return False, "universe_contract_invalid"

    ranks: list[int] = []
    for item in symbols:
        if item.get("state") not in VALID_STATES:
            return False, "invalid_state"
        if item.get("rotation_context") not in VALID_ROTATION:
            return False, "invalid_rotation_context"
        score = float(item.get("rs_score", -1))
        if not 0.0 <= score <= 100.0:
            return False, "invalid_rs_score"
        ranks.append(int(item.get("rank") or 0))
        for horizon in (7, 30, 90):
            required = (
                f"return_{horizon}d_pct",
                f"percentile_{horizon}d",
                f"relative_to_btc_{horizon}d_pct",
                f"relative_to_universe_{horizon}d_pct",
            )
            if any(field not in item for field in required):
                return False, "missing_relative_strength_field"
    if sorted(ranks) != list(range(1, universe_size + 1)):
        return False, "rank_sequence_invalid"
    if float(payload.get("coverage_pct") or 0.0) < 60.0:
        return False, "coverage_too_low"
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
    for attempt in range(MAX_POLLS):
        try:
            version = _call(fetch, base_url, "/version", api_key, timeout)
        except (HTTPError, URLError, TimeoutError, OSError):
            version = {}
        if version.get("commit_sha") == expected_sha:
            break
        if attempt + 1 == MAX_POLLS:
            print("FAIL phase=version reason=expected_commit_not_deployed")
            return 1
        sleep(POLL_INTERVAL_SECONDS)

    try:
        capture = _call(
            fetch,
            base_url,
            "/v1/research/relative-strength/capture",
            api_key,
            timeout,
            "POST",
        )
    except HTTPError as exc:
        print(f"FAIL phase=capture http_status={exc.code}")
        return 1
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"FAIL phase=capture error_type={type(exc).__name__}")
        return 1

    ok, reason = validate_capture(capture, expected_sha)
    if not ok:
        print(f"FAIL phase=capture reason={reason}")
        print(
            "CAPTURE_SAFE="
            + json.dumps(
                {
                    "universe_size": capture.get("universe_size"),
                    "coverage_pct": capture.get("coverage_pct"),
                    "requested_symbols": capture.get("requested_symbols"),
                    "analyzed_symbols": capture.get("analyzed_symbols"),
                    "failed_symbols": capture.get("failed_symbols"),
                },
                sort_keys=True,
            )
        )
        return 1

    status = _call(
        fetch, base_url, "/v1/research/relative-strength/status", api_key, timeout
    )
    latest = status.get("latest") or {}
    if int(status.get("snapshot_count") or 0) < 1:
        print("FAIL phase=status reason=no_snapshot")
        return 1
    if latest.get("captured_day") != capture.get("captured_day"):
        print("FAIL phase=status reason=captured_day_mismatch")
        return 1
    if latest.get("source_commit_sha") != expected_sha:
        print("FAIL phase=status reason=persisted_exact_sha_mismatch")
        return 1

    rows = list(capture.get("symbols") or [])
    compact = lambda item: {
        "rank": item.get("rank"),
        "symbol": item.get("symbol"),
        "state": item.get("state"),
        "rs_score": item.get("rs_score"),
        "return_7d_pct": item.get("return_7d_pct"),
        "return_30d_pct": item.get("return_30d_pct"),
        "return_90d_pct": item.get("return_90d_pct"),
        "relative_to_btc_30d_pct": item.get("relative_to_btc_30d_pct"),
        "rotation_context": item.get("rotation_context"),
    }
    safe = {
        "captured_at": capture.get("captured_at"),
        "captured_day": capture.get("captured_day"),
        "source_commit_sha": capture.get("source_commit_sha"),
        "universe_size": capture.get("universe_size"),
        "coverage_pct": capture.get("coverage_pct"),
        "breadth": capture.get("breadth"),
        "state_counts": capture.get("state_counts"),
        "rotation_counts": capture.get("rotation_counts"),
        "leaders": [compact(item) for item in rows[:5]],
        "laggards": [compact(item) for item in rows[-5:]],
        "sector_metadata_status": capture.get("sector_metadata_status"),
    }
    print("RELATIVE_STRENGTH_SHADOW=" + json.dumps(safe, sort_keys=True))
    print("RELATIVE STRENGTH SHADOW V1 VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required relative-strength smoke configuration is missing")
        return 1
    return run_smoke(base_url, api_key, expected_sha)


if __name__ == "__main__":
    sys.exit(main())
