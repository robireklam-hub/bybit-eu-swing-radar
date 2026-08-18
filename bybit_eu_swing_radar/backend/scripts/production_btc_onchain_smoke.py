#!/usr/bin/env python3
"""Exact-SHA production smoke for BTC On-Chain Context v1."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
from urllib.request import Request, urlopen


def _request(base: str, path: str, key: str, *, method: str = "GET", timeout: float = 35.0) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "btc-onchain-production-smoke/1"}
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


def main() -> int:
    base = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    key = os.getenv("PRODUCTION_RADAR_API_KEY", "").strip()
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base or not key or not expected_sha:
        print("FAIL missing production smoke configuration")
        return 1

    for attempt in range(40):
        try:
            version = _request(base, "/version", "", timeout=15)
            if version.get("commit_sha") == expected_sha:
                break
        except Exception:
            pass
        if attempt == 39:
            print("FAIL exact production API SHA not serving")
            return 1
        time.sleep(4)

    spec = _request(base, "/v1/research/btc-onchain/spec", key, timeout=20)
    if spec.get("spec_version") != "btc-onchain-context-shadow-v1":
        print("FAIL unexpected BTC on-chain spec version")
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

    capture = _request(base, "/v1/research/btc-onchain/capture", key, method="POST", timeout=45)
    safe_capture = {
        "spec_version": capture.get("spec_version"),
        "captured_at": capture.get("captured_at"),
        "source_commit_sha": capture.get("source_commit_sha"),
        "persisted": capture.get("persisted"),
        "data_quality": capture.get("data_quality"),
        "source_status": capture.get("source_status"),
        "coin_metrics": capture.get("coin_metrics"),
        "mempool": capture.get("mempool"),
        "recommended_fees": capture.get("recommended_fees"),
        "difficulty_adjustment": capture.get("difficulty_adjustment"),
    }
    print("BTC_ONCHAIN_CAPTURE=" + json.dumps(safe_capture, sort_keys=True))

    if capture.get("source_commit_sha") != expected_sha or capture.get("persisted") is not True:
        print("FAIL BTC on-chain capture not persisted on exact SHA")
        return 1
    if capture.get("promotion_allowed") is not False or capture.get("live_strategy_mutated") is not False:
        print("FAIL BTC on-chain live/promotion guard changed")
        return 1
    if _forbidden_key(capture):
        print("FAIL forbidden directional/trading field present")
        return 1

    cm = capture.get("coin_metrics") or {}
    if int(cm.get("available_metric_count") or 0) < 4:
        print("FAIL insufficient Coin Metrics community coverage")
        return 1
    statuses = capture.get("source_status") or {}
    if (statuses.get("coin_metrics") or {}).get("status") not in {"LIVE", "PARTIAL"}:
        print("FAIL Coin Metrics source unavailable")
        return 1
    mempool_live = sum(
        1
        for name in ("mempool", "recommended_fees", "difficulty_adjustment")
        if (statuses.get(name) or {}).get("status") == "LIVE"
    )
    if mempool_live < 2:
        print("FAIL insufficient mempool.space source coverage")
        return 1

    status = _request(base, "/v1/research/btc-onchain/status", key, timeout=25)
    latest = status.get("latest") or {}
    print(
        "BTC_ONCHAIN_STATUS="
        + json.dumps(
            {
                "snapshot_count": status.get("snapshot_count"),
                "latest_captured_at": latest.get("captured_at"),
                "latest_source_commit_sha": latest.get("source_commit_sha"),
                "latest_data_quality": latest.get("data_quality"),
            },
            sort_keys=True,
        )
    )
    if latest.get("source_commit_sha") != expected_sha:
        print("FAIL BTC on-chain status did not read back exact-SHA snapshot")
        return 1
    if status.get("promotion_allowed") is not False or status.get("live_strategy_mutated") is not False:
        print("FAIL BTC on-chain status guard changed")
        return 1

    print("BTC ONCHAIN CONTEXT SHADOW V1 VERIFIED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
