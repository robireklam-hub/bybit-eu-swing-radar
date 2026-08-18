#!/usr/bin/env python3
"""Exact-SHA production smoke for Cross-Layer Context v2."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
from urllib.request import Request, urlopen

NEW_LAYERS = {"sector_rotation", "btc_onchain", "eth_onchain"}


def _request(base: str, path: str, key: str, *, method: str = "GET", timeout: float = 45.0) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "cross-layer-v2-production-smoke/1"}
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

    for attempt in range(45):
        try:
            if _request(base, "/version", "", timeout=15).get("commit_sha") == expected_sha:
                break
        except Exception:
            pass
        if attempt == 44:
            print("FAIL exact production API SHA not serving")
            return 1
        time.sleep(4)

    spec = _request(base, "/v1/research/cross-layer-context-v2/spec", key, timeout=20)
    if spec.get("version") != "cross-layer-context-shadow-v2":
        print("FAIL unexpected cross-layer v2 spec")
        return 1
    if set(spec.get("new_vs_v1") or []) != NEW_LAYERS:
        print("FAIL v2 layer expansion contract changed")
        return 1
    for field, expected in {
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "composite_score_emitted": False,
        "execution_proof": False,
    }.items():
        if spec.get(field) != expected:
            print(f"FAIL spec guard changed: {field}")
            return 1

    capture = _request(base, "/v1/research/cross-layer-context-v2/capture", key, method="POST", timeout=60)
    layers = capture.get("layers") or {}
    safe = {
        "spec_version": capture.get("spec_version"),
        "captured_at": capture.get("captured_at"),
        "source_commit_sha": capture.get("source_commit_sha"),
        "persisted": capture.get("persisted"),
        "data_quality": capture.get("data_quality"),
        "layer_fresh_count": capture.get("layer_fresh_count"),
        "layer_count": capture.get("layer_count"),
        "new_layer_status": {name: layers.get(name) for name in sorted(NEW_LAYERS)},
        "symbol_count": capture.get("symbol_count"),
        "sector_global": (capture.get("global_context") or {}).get("sector_rotation"),
    }
    print("CROSS_LAYER_V2_CAPTURE=" + json.dumps(safe, sort_keys=True))
    if capture.get("source_commit_sha") != expected_sha or capture.get("persisted") is not True:
        print("FAIL cross-layer v2 not persisted on exact SHA")
        return 1
    if int(capture.get("layer_count") or 0) != 8:
        print("FAIL cross-layer v2 layer count is not 8")
        return 1
    for name in NEW_LAYERS:
        if (layers.get(name) or {}).get("status") != "FRESH":
            print(f"FAIL new layer not FRESH: {name}")
            return 1
    if capture.get("promotion_allowed") is not False or capture.get("composite_score_emitted") is not False:
        print("FAIL cross-layer v2 promotion/composite guard changed")
        return 1
    if _forbidden_key(capture):
        print("FAIL forbidden trading field present")
        return 1

    rows = {str(row.get("symbol")): row for row in (capture.get("symbols") or []) if isinstance(row, dict)}
    if not (rows.get("BTCUSDC") or {}).get("onchain"):
        print("FAIL BTC on-chain not joined to BTCUSDC")
        return 1
    if not (rows.get("ETHUSDC") or {}).get("onchain"):
        print("FAIL ETH on-chain not joined to ETHUSDC")
        return 1
    if not any((row.get("sector_rotation") or {}).get("functional_tags") for row in rows.values()):
        print("FAIL sector taxonomy not joined to any symbol")
        return 1

    status = _request(base, "/v1/research/cross-layer-context-v2/status", key, timeout=25)
    latest = status.get("latest") or {}
    print("CROSS_LAYER_V2_STATUS=" + json.dumps({"snapshot_count": status.get("snapshot_count"), "latest_source_commit_sha": latest.get("source_commit_sha"), "latest_data_quality": latest.get("data_quality"), "latest_layer_fresh_count": latest.get("layer_fresh_count")}, sort_keys=True))
    if latest.get("source_commit_sha") != expected_sha:
        print("FAIL cross-layer v2 status exact-SHA readback failed")
        return 1
    print("CROSS-LAYER CONTEXT V2 VERIFIED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
