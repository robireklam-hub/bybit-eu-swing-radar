#!/usr/bin/env python3
"""Read-only production check for preregistered v0.7.7 microstructure cohort."""
from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PREREGISTERED_STRATEGY_VERSION = "0.7.7"
SPEC_VERSION = "microstructure-forward-alignment-v5"
PARENT_SPEC_VERSION = "microstructure-forward-alignment-v4"
EXACT_PRODUCTION_VERIFIER_PR = 468
EXPECTED_STRATEGY_MERGE_SHA = "04116db76f92dc1738071c9e5d774b55b69a1fc2"
EXPECTED_COHORT_START_AT = "2026-08-22T05:55:00+00:00"
EXPECTED_SYMBOLS = ("BTCUSDC", "ETHUSDC", "SOLUSDC")


def fetch_json(url: str, api_key: str, timeout: float = 15.0) -> dict[str, Any]:
    req = Request(url, method="GET", headers={"Accept":"application/json","User-Agent":"bybit-eu-microstructure-alignment-status-v5/1","X-Radar-Key":api_key})
    with urlopen(req, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def validate_alignment_status(payload: dict[str, Any]) -> tuple[bool, str]:
    invariants = {
        "research_only": True,
        "live_strategy_mutated": False,
        "label_blind": True,
        "post_signal_data_used": False,
        "outcome_visible": False,
        "promotion_allowed": False,
        "threshold_search_allowed": False,
        "strategy_version_isolated": True,
        "preregistered_strategy_version": PREREGISTERED_STRATEGY_VERSION,
    }
    for field, expected in invariants.items():
        if payload.get(field) != expected:
            return False, f"unexpected_{field}"
    if payload.get("error") or payload.get("error_type"):
        return False, "alignment_query_error"
    spec = payload.get("spec")
    if not isinstance(spec, dict):
        return False, "spec_missing"
    expected_spec = {
        "spec_version": SPEC_VERSION,
        "parent_spec_version": PARENT_SPEC_VERSION,
        "preregistered_strategy_version": PREREGISTERED_STRATEGY_VERSION,
        "cohort_start_at": EXPECTED_COHORT_START_AT,
        "forward_only": True,
        "label_blind": True,
        "outcome_visible": False,
        "promotion_allowed": False,
        "threshold_search_allowed": False,
    }
    for field, expected in expected_spec.items():
        if spec.get(field) != expected:
            return False, f"unexpected_spec_{field}"
    evidence = spec.get("production_activation_evidence")
    if not isinstance(evidence, dict) or evidence.get("exact_production_verifier_pr") != EXACT_PRODUCTION_VERIFIER_PR or evidence.get("strategy_merge_sha") != EXPECTED_STRATEGY_MERGE_SHA:
        return False, "production_activation_evidence_mutated"
    sample = payload.get("sample")
    if not isinstance(sample, dict) or sample.get("minimum_total") != 60 or sample.get("minimum_per_symbol") != 10:
        return False, "sample_gate_mutated"
    per_symbol = sample.get("per_symbol")
    if not isinstance(per_symbol, dict) or set(per_symbol) != set(EXPECTED_SYMBOLS):
        return False, "sample_symbol_partition_mutated"
    total = sample.get("total_signals")
    if not isinstance(total, int) or total < 0 or sum(per_symbol.values()) != total:
        return False, "sample_counts_invalid"
    expected_sample_ready = total >= 60 and all(per_symbol[s] >= 10 for s in EXPECTED_SYMBOLS)
    if sample.get("ready_for_preregistered_effect_test") is not expected_sample_ready:
        return False, "sample_readiness_inconsistent"
    coverage = payload.get("alignment_coverage")
    if not isinstance(coverage, dict):
        return False, "alignment_coverage_missing"
    journal = coverage.get("journal_signal_count")
    aligned = coverage.get("aligned_signal_count")
    unaligned = coverage.get("unaligned_signal_count")
    if not all(isinstance(v, int) and v >= 0 for v in (journal, aligned, unaligned)) or journal != aligned + unaligned or aligned != total:
        return False, "alignment_counts_invalid"
    status = coverage.get("status")
    if journal == 0 and status != "WAITING_FOR_FORWARD_SIGNALS":
        return False, "waiting_state_invalid"
    if journal > 0 and unaligned == 0 and status != "ALIGNED":
        return False, "aligned_state_invalid"
    if unaligned > 0 and status != "COVERAGE_FAILURE":
        return False, "coverage_failure_state_invalid"
    expected_ready = payload.get("data_quality_ready") is True and status == "ALIGNED" and expected_sample_ready
    if payload.get("ready_for_preregistered_effect_test") is not expected_ready:
        return False, "effect_readiness_inconsistent"
    return True, "ok"


def main() -> int:
    base = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip().rstrip("/")
    key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base or not key or not expected_sha:
        print("FAIL required alignment v5 status configuration is missing")
        return 1
    try:
        version = fetch_json(f"{base}/version", key)
        if version.get("commit_sha") != expected_sha:
            print("FAIL phase=version reason=expected_commit_not_deployed")
            return 1
        payload = fetch_json(f"{base}/v1/research/microstructure/alignment-status-v5", key)
    except HTTPError as exc:
        print(f"FAIL phase=alignment_v5 http_status={exc.code}")
        return 1
    except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL phase=alignment_v5 error_type={type(exc).__name__}")
        return 1
    ok, reason = validate_alignment_status(payload)
    spec = payload.get("spec") or {}
    safe = {k: payload.get(k) for k in ("data_quality_ready","alignment_coverage_ready","alignment_coverage","ready_for_preregistered_effect_test","sample","interval","preregistered_strategy_version","strategy_version_isolated","outcome_visible","promotion_allowed","threshold_search_allowed")}
    safe["spec_version"] = spec.get("spec_version")
    safe["parent_spec_version"] = spec.get("parent_spec_version")
    print("ALIGNMENT_V5_STATUS=" + json.dumps(safe, sort_keys=True))
    if not ok:
        print(f"FAIL phase=alignment_v5 reason={reason}")
        return 1
    print("MICROSTRUCTURE V5 ALIGNMENT SAMPLE STATUS VERIFIED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
