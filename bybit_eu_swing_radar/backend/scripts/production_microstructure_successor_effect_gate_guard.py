#!/usr/bin/env python3
"""Fail-closed production guard for preregistered microstructure successor cohorts.

This intentionally does not read outcomes. It verifies that v0.7.4/v0.7.5
alignment cohorts may become sample-ready without exposing outcomes, enabling
promotion, or enabling threshold search before a separately preregistered effect
analysis is implemented.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

COHORTS = (
    ("v2", "0.7.4", "/v1/research/microstructure/alignment-status-v2"),
    ("v3", "0.7.5", "/v1/research/microstructure/alignment-status-v3"),
)


def fetch_json(url: str, api_key: str, timeout: float = 15.0) -> dict[str, Any]:
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "bybit-eu-microstructure-successor-effect-gate-guard/1",
            "X-Radar-Key": api_key,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def validate_successor_gate(payload: dict[str, Any], strategy_version: str) -> tuple[bool, str]:
    invariants = {
        "research_only": True,
        "live_strategy_mutated": False,
        "label_blind": True,
        "post_signal_data_used": False,
        "outcome_visible": False,
        "promotion_allowed": False,
        "strategy_version_isolated": True,
        "preregistered_strategy_version": strategy_version,
    }
    for field, expected in invariants.items():
        if payload.get(field) != expected:
            return False, f"unexpected_{field}"
    if payload.get("error") or payload.get("error_type"):
        return False, "alignment_query_error"

    spec = payload.get("spec")
    if not isinstance(spec, dict):
        return False, "spec_missing"
    if spec.get("outcome_visible") is not False or spec.get("promotion_allowed") is not False:
        return False, "spec_outcome_or_promotion_gate_open"
    if strategy_version == "0.7.5":
        if payload.get("threshold_search_allowed") is not False:
            return False, "threshold_search_gate_open"
        if spec.get("threshold_search_allowed") is not False:
            return False, "spec_threshold_search_gate_open"

    sample = payload.get("sample")
    coverage = payload.get("alignment_coverage")
    if not isinstance(sample, dict) or not isinstance(coverage, dict):
        return False, "sample_or_coverage_missing"
    if sample.get("minimum_total") != 60 or sample.get("minimum_per_symbol") != 10:
        return False, "sample_gate_mutated"

    ready = payload.get("ready_for_preregistered_effect_test")
    if not isinstance(ready, bool):
        return False, "effect_readiness_not_boolean"
    coverage_ready = payload.get("alignment_coverage_ready") is True
    data_quality_ready = payload.get("data_quality_ready") is True
    sample_ready = sample.get("ready_for_preregistered_effect_test") is True
    expected_ready = data_quality_ready and coverage_ready and sample_ready
    if ready is not expected_ready:
        return False, "effect_readiness_inconsistent"
    return True, "sample_ready_outcomes_still_closed" if ready else "waiting_outcomes_closed"


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required successor effect-gate configuration is missing")
        return 1
    try:
        version = fetch_json(f"{base_url}/version", api_key)
        if version.get("commit_sha") != expected_sha:
            print("FAIL phase=version reason=expected_commit_not_deployed")
            return 1
        results: dict[str, Any] = {}
        for cohort, strategy_version, path in COHORTS:
            payload = fetch_json(base_url + path, api_key)
            ok, reason = validate_successor_gate(payload, strategy_version)
            results[cohort] = {
                "strategy_version": strategy_version,
                "ready_for_preregistered_effect_test": payload.get("ready_for_preregistered_effect_test"),
                "outcome_visible": payload.get("outcome_visible"),
                "promotion_allowed": payload.get("promotion_allowed"),
                "threshold_search_allowed": payload.get("threshold_search_allowed"),
                "sample": payload.get("sample"),
                "alignment_coverage": payload.get("alignment_coverage"),
                "guard_reason": reason,
            }
            if not ok:
                print("SUCCESSOR_EFFECT_GATE_GUARD=" + json.dumps(results, sort_keys=True))
                print(f"FAIL phase={cohort} reason={reason}")
                return 1
    except HTTPError as exc:
        print(f"FAIL phase=successor_effect_gate http_status={exc.code}")
        return 1
    except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL phase=successor_effect_gate error_type={type(exc).__name__}")
        return 1

    print("SUCCESSOR_EFFECT_GATE_GUARD=" + json.dumps(results, sort_keys=True))
    print("MICROSTRUCTURE SUCCESSOR EFFECT GATES VERIFIED CLOSED-UNTIL-READY.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
