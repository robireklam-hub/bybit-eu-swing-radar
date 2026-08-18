#!/usr/bin/env python3
"""Exact-SHA production smoke for Cross-Layer Context v1."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get(
    "PRODUCTION_RADAR_API_BASE_URL",
    "https://bybit-eu-swing-radar-production.up.railway.app",
).rstrip("/")
KEY = os.environ.get("PRODUCTION_RADAR_API_KEY", "")
EXPECTED_SHA = os.environ.get("EXPECTED_SHA", "").strip()


def request_json(path: str, *, method: str = "GET") -> dict:
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=b"{}" if method == "POST" else None,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Radar-Key": KEY,
        },
    )
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)


def fail(phase: str, reason: str, payload: dict | None = None) -> None:
    print(f"FAIL phase={phase} reason={reason}")
    if payload is not None:
        print("PAYLOAD_SAFE=" + json.dumps(payload, sort_keys=True, default=str))
    raise SystemExit(1)


def wait_for_exact_sha() -> None:
    if not EXPECTED_SHA:
        fail("version", "EXPECTED_SHA_missing")
    deadline = time.monotonic() + 150.0
    last = None
    while time.monotonic() < deadline:
        try:
            last = request_json("/version")
        except Exception as exc:
            last = {"error_type": type(exc).__name__}
            time.sleep(5)
            continue
        if last.get("commit_sha") == EXPECTED_SHA:
            return
        time.sleep(5)
    fail("version", "exact_SHA_not_live", last)


def validate_capture(payload: dict, expected_sha: str) -> tuple[bool, str]:
    if payload.get("source_commit_sha") != expected_sha:
        return False, "source_SHA_mismatch"
    if payload.get("persisted") is not True:
        return False, "not_persisted"
    if payload.get("research_only") is not True:
        return False, "research_only_false"
    if payload.get("label_free") is not True:
        return False, "label_free_false"
    if payload.get("live_strategy_mutated") is not False:
        return False, "live_strategy_mutated"
    if payload.get("promotion_allowed") is not False:
        return False, "promotion_guard_failed"
    if payload.get("composite_score_emitted") is not False:
        return False, "composite_score_emitted"
    if payload.get("execution_proof") is not False:
        return False, "execution_proof_true"
    if int(payload.get("layer_count") or 0) != 5:
        return False, "layer_count_not_five"
    layers = payload.get("layers") or {}
    if set(layers) != {
        "market_regime",
        "derivatives_positioning",
        "relative_strength",
        "event_tokenomics",
        "btc_macro_cycle_etf",
    }:
        return False, "layer_set_mismatch"
    if any((meta or {}).get("status") in {"FUTURE_REJECTED", "INVALID_TIMESTAMP"} for meta in layers.values()):
        return False, "temporal_integrity_failed"
    if int(payload.get("layer_fresh_count") or 0) < 3:
        return False, "insufficient_fresh_layers"
    if int(payload.get("symbol_count") or 0) < 8:
        return False, "insufficient_symbol_join"
    micro = payload.get("microstructure") or {}
    if micro.get("joined") is not False or micro.get("policy") != "SIGNAL_TIME_PRE_SIGNAL_ONLY_NOT_SNAPSHOT_JOINED":
        return False, "microstructure_policy_failed"
    for row in payload.get("symbols") or []:
        if "score" in row or "decision" in row or "eligibility" in row:
            return False, "forbidden_composite_output"
        if not str(row.get("symbol") or "").endswith("USDC"):
            return False, "non_USDC_symbol"
    return True, "ok"


def main() -> None:
    if not KEY:
        fail("config", "PRODUCTION_RADAR_API_KEY_missing")
    wait_for_exact_sha()

    spec = request_json("/v1/research/cross-layer-context/spec")
    if spec.get("version") != "cross-layer-context-shadow-v1":
        fail("spec", "wrong_spec_version", spec)
    if spec.get("composite_score_emitted") is not False or spec.get("promotion_allowed") is not False:
        fail("spec", "research_contract_failed", spec)
    if spec.get("microstructure_join_policy") != "SIGNAL_TIME_PRE_SIGNAL_ONLY_NOT_SNAPSHOT_JOINED":
        fail("spec", "microstructure_policy_failed", spec)

    capture = request_json("/v1/research/cross-layer-context/capture", method="POST")
    ok, reason = validate_capture(capture, EXPECTED_SHA)
    if not ok:
        fail("capture", reason, capture)

    status = request_json("/v1/research/cross-layer-context/status")
    latest = status.get("latest") or {}
    if latest.get("source_commit_sha") != EXPECTED_SHA:
        fail("status", "latest_SHA_mismatch", status)
    if status.get("promotion_allowed") is not False or status.get("live_strategy_mutated") is not False:
        fail("status", "research_contract_failed", status)

    safe = {
        "source_commit_sha": capture.get("source_commit_sha"),
        "captured_at": capture.get("captured_at"),
        "data_quality": capture.get("data_quality"),
        "layer_fresh_count": capture.get("layer_fresh_count"),
        "symbol_count": capture.get("symbol_count"),
        "layers": {
            name: {
                "status": meta.get("status"),
                "age_seconds": meta.get("age_seconds"),
                "source_commit_sha": meta.get("source_commit_sha"),
            }
            for name, meta in (capture.get("layers") or {}).items()
        },
        "leaders": ((capture.get("global_context") or {}).get("relative_strength") or {}).get("leaders"),
        "global_regime": ((capture.get("global_context") or {}).get("market_regime") or {}).get("global_regime"),
    }
    print("CROSS_LAYER_CONTEXT=" + json.dumps(safe, sort_keys=True, default=str))
    print("CROSS-LAYER CONTEXT V1 VERIFIED.")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        print(f"FAIL http_status={exc.code}")
        sys.exit(1)
