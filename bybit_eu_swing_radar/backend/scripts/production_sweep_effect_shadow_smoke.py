#!/usr/bin/env python3
"""Exact-SHA production smoke for prospective Sweep Effect v1."""
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


def request_json(path: str) -> dict:
    req = urllib.request.Request(
        BASE + path,
        headers={"Accept": "application/json", "X-Radar-Key": KEY},
    )
    with urllib.request.urlopen(req, timeout=40) as response:
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
        except Exception as exc:  # deployment may be between revisions
            last = {"error_type": type(exc).__name__}
            time.sleep(5)
            continue
        if last.get("commit_sha") == EXPECTED_SHA:
            return
        time.sleep(5)
    fail("version", "exact_SHA_not_live", last)


def main() -> None:
    if not KEY:
        fail("config", "PRODUCTION_RADAR_API_KEY_missing")
    wait_for_exact_sha()

    spec = request_json("/v1/research/sweep-effect/spec")
    if spec.get("spec_version") != "sweep-forward-effect-v1":
        fail("spec", "wrong_spec_version", spec)
    required_false = ["live_strategy_mutated", "promotion_allowed"]
    if any(spec.get(key) is not False for key in required_false):
        fail("spec", "research_contract_failed", spec)
    if spec.get("research_only") is not True or spec.get("label_gate_before_outcomes") is not True:
        fail("spec", "research_contract_failed", spec)
    if (spec.get("sample_gate") or {}).get("minimum_closed_signals") != 60:
        fail("spec", "sample_gate_mutated", spec)

    status = request_json("/v1/research/sweep-effect/status")
    if status.get("source_commit_sha") != EXPECTED_SHA:
        fail("status", "source_SHA_mismatch", status)
    if status.get("promotion_allowed") is not False or status.get("live_strategy_mutated") is not False:
        fail("status", "research_contract_failed", status)
    state = status.get("status")
    if state not in {
        "WAITING_FOR_FORWARD_SAMPLE",
        "WAITING_FOR_HYPOTHESIS_COVERAGE",
        "COMPLETE",
    }:
        fail("status", "unexpected_status", status)

    gate = ((status.get("sample") or {}).get("gate") or {})
    if not isinstance(gate.get("closed_signal_count"), int):
        fail("status", "sample_counts_missing", status)
    if state == "WAITING_FOR_FORWARD_SAMPLE":
        if gate.get("ready") is not False or status.get("outcomes_loaded") is not False:
            fail("status", "label_gate_not_fail_closed", status)
        if status.get("effects") is not None:
            fail("status", "effects_present_below_gate", status)
    else:
        if gate.get("ready") is not True or status.get("outcomes_loaded") is not True:
            fail("status", "outcome_gate_inconsistent", status)
        effects = status.get("effects") or {}
        if len(effects.get("hypotheses") or []) != 4:
            fail("status", "hypothesis_contract_failed", status)
        if effects.get("promotion_allowed") is not False:
            fail("status", "promotion_guard_failed", status)

    safe = {
        "status": state,
        "source_commit_sha": status.get("source_commit_sha"),
        "closed_signal_count": gate.get("closed_signal_count"),
        "long_count": gate.get("long_count"),
        "short_count": gate.get("short_count"),
        "distinct_utc_days": gate.get("distinct_utc_days"),
        "attribute_coverage_pct": gate.get("attribute_coverage_pct"),
        "gate_ready": gate.get("ready"),
        "outcomes_loaded": status.get("outcomes_loaded"),
    }
    print("SWEEP_EFFECT_STATUS=" + json.dumps(safe, sort_keys=True))
    print("SWEEP FORWARD EFFECT V1 VERIFIED.")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        print(f"FAIL http_status={exc.code}")
        sys.exit(1)
