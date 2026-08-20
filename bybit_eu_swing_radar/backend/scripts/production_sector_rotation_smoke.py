#!/usr/bin/env python3
"""Exact-SHA production smoke for sourced Sector Rotation Shadow v1."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
from urllib.request import Request, urlopen


def _request(base: str, path: str, key: str, *, method: str = "GET", timeout: float = 90.0) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "sector-rotation-production-smoke/1"}
    if key:
        headers["X-Radar-Key"] = key
    request = Request(base.rstrip("/") + path, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode())
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _forbidden_key(value: Any) -> str | None:
    forbidden = {"bull_bear_score", "trade_signal", "eligibility_gate", "execution_allowed"}
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in forbidden:
                return str(key)
            nested = _forbidden_key(child)
            if nested:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _forbidden_key(child)
            if nested:
                return nested
    return None


def _immutable_history_error(capture: dict[str, Any]) -> str | None:
    """Validate that the production capture was also written to append-only history."""
    history = capture.get("immutable_history")
    if not isinstance(history, dict):
        return "immutable_history missing"
    if history.get("immutable") is not True:
        return "immutable_history is not immutable"
    if history.get("purpose") != "append_only_raw_history":
        return "immutable_history purpose changed"
    if history.get("research_family") != "sector-rotation":
        return "immutable_history research family changed"
    if history.get("spec_version") != "sector-rotation-shadow-v1":
        return "immutable_history spec version changed"
    if history.get("captured_at") != capture.get("captured_at"):
        return "immutable_history captured_at mismatch"
    fingerprint = str(history.get("payload_fingerprint") or "")
    if len(fingerprint) != 64:
        return "immutable_history payload fingerprint missing"
    if int(history.get("history_count") or 0) < 1:
        return "immutable_history count is empty"
    if int(history.get("bucket_history_count") or 0) < 1:
        return "immutable_history bucket count is empty"
    return None


def main() -> int:
    base = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    key = os.getenv("PRODUCTION_RADAR_API_KEY", "").strip()
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base or not key or not expected_sha:
        print("FAIL missing production smoke configuration")
        return 1

    for attempt in range(45):
        try:
            version = _request(base, "/version", "", timeout=15)
            if version.get("commit_sha") == expected_sha:
                break
        except Exception:
            pass
        if attempt == 44:
            print("FAIL exact production API SHA not serving")
            return 1
        time.sleep(4)

    spec = _request(base, "/v1/research/sector-rotation/spec", key, timeout=20)
    if spec.get("spec_version") != "sector-rotation-shadow-v1":
        print("FAIL unexpected sector-rotation spec version")
        return 1
    for field, expected in {
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "execution_proof": False,
        "promotion_allowed": False,
    }.items():
        if spec.get(field) != expected:
            print(f"FAIL spec guard changed: {field}")
            return 1
    taxonomy = spec.get("taxonomy") or {}
    if taxonomy.get("provider") != "CoinPaprika" or taxonomy.get("hand_labels_allowed") is not False:
        print("FAIL sourced taxonomy contract changed")
        return 1

    capture = _request(base, "/v1/research/sector-rotation/capture", key, method="POST", timeout=100)
    history = capture.get("immutable_history") or {}
    safe_capture = {
        "spec_version": capture.get("spec_version"),
        "captured_at": capture.get("captured_at"),
        "source_commit_sha": capture.get("source_commit_sha"),
        "persisted": capture.get("persisted"),
        "data_quality": capture.get("data_quality"),
        "universe_size": capture.get("universe_size"),
        "resolved_symbol_count": capture.get("resolved_symbol_count"),
        "taxonomy_mapped_symbol_count": capture.get("taxonomy_mapped_symbol_count"),
        "ambiguous_resolution_count": capture.get("ambiguous_resolution_count"),
        "resolution_coverage_pct": capture.get("resolution_coverage_pct"),
        "taxonomy_coverage_pct": capture.get("taxonomy_coverage_pct"),
        "sector_group_count": capture.get("sector_group_count"),
        "rotation_ranked_group_count": capture.get("rotation_ranked_group_count"),
        "sector_rotation_available": capture.get("sector_rotation_available"),
        "source_status": capture.get("source_status"),
        "top_relative_strength_groups": capture.get("top_relative_strength_groups"),
        "immutable_history": {
            "immutable": history.get("immutable"),
            "research_family": history.get("research_family"),
            "spec_version": history.get("spec_version"),
            "captured_at": history.get("captured_at"),
            "history_count": history.get("history_count"),
            "bucket_history_count": history.get("bucket_history_count"),
        },
    }
    print("SECTOR_ROTATION_CAPTURE=" + json.dumps(safe_capture, sort_keys=True))

    if capture.get("source_commit_sha") != expected_sha or capture.get("persisted") is not True:
        print("FAIL sector-rotation capture not persisted on exact SHA")
        return 1
    history_error = _immutable_history_error(capture)
    if history_error:
        print(f"FAIL sector-rotation immutable history: {history_error}")
        return 1
    if capture.get("promotion_allowed") is not False or capture.get("live_strategy_mutated") is not False:
        print("FAIL sector-rotation live/promotion guard changed")
        return 1
    if _forbidden_key(capture):
        print("FAIL forbidden directional/trading field present")
        return 1

    source_status = capture.get("source_status") or {}
    for name in ("relative_strength", "coinpaprika_tickers", "coinpaprika_tags"):
        if (source_status.get(name) or {}).get("status") != "LIVE":
            print(f"FAIL required source not LIVE: {name}")
            return 1
    universe_size = int(capture.get("universe_size") or 0)
    resolved = int(capture.get("resolved_symbol_count") or 0)
    mapped = int(capture.get("taxonomy_mapped_symbol_count") or 0)
    if universe_size < 12:
        print("FAIL insufficient relative-strength universe")
        return 1
    if resolved / universe_size < 0.50:
        print("FAIL insufficient provider symbol resolution coverage")
        return 1
    if mapped / universe_size < 0.50:
        print("FAIL insufficient functional taxonomy coverage")
        return 1
    if capture.get("sector_rotation_available") is not True:
        print("FAIL no multi-coin sector rotation groups available")
        return 1
    if int(capture.get("rotation_ranked_group_count") or 0) < 2:
        print("FAIL fewer than two multi-coin rotation groups")
        return 1

    status = _request(base, "/v1/research/sector-rotation/status", key, timeout=30)
    latest = status.get("latest") or {}
    print(
        "SECTOR_ROTATION_STATUS="
        + json.dumps(
            {
                "snapshot_count": status.get("snapshot_count"),
                "latest_captured_at": latest.get("captured_at"),
                "latest_source_commit_sha": latest.get("source_commit_sha"),
                "latest_taxonomy_coverage_pct": latest.get("taxonomy_coverage_pct"),
                "latest_rotation_ranked_group_count": latest.get("rotation_ranked_group_count"),
            },
            sort_keys=True,
        )
    )
    if latest.get("source_commit_sha") != expected_sha:
        print("FAIL sector-rotation status did not read back exact-SHA snapshot")
        return 1
    if status.get("promotion_allowed") is not False or status.get("live_strategy_mutated") is not False:
        print("FAIL sector-rotation status guard changed")
        return 1

    print("SECTOR ROTATION SHADOW V1 VERIFIED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
