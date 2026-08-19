#!/usr/bin/env python3
"""Read-only production check for preregistered microstructure alignment sample readiness."""
from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PREREGISTERED_STRATEGY_VERSION = "0.7.3"


def fetch_json(url: str, api_key: str, timeout: float = 15.0) -> dict[str, Any]:
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "bybit-eu-microstructure-alignment-status/1",
            "X-Radar-Key": api_key,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def validate_alignment_status(payload: dict[str, Any]) -> tuple[bool, str]:
    if payload.get("research_only") is not True:
        return False, "research_only_not_true"
    if payload.get("live_strategy_mutated") is not False:
        return False, "live_strategy_mutated_not_false"
    if payload.get("label_blind") is not True:
        return False, "label_blind_not_true"
    if payload.get("post_signal_data_used") is not False:
        return False, "post_signal_data_used_not_false"
    if payload.get("promotion_allowed") is not False:
        return False, "promotion_allowed_not_false"
    if payload.get("error") or payload.get("error_type"):
        return False, "alignment_query_error"
    spec = payload.get("spec")
    if not isinstance(spec, dict) or spec.get("spec_version") != "microstructure-forward-alignment-v1":
        return False, "unexpected_alignment_spec"
    if spec.get("preregistered_strategy_version") != PREREGISTERED_STRATEGY_VERSION:
        return False, "unexpected_preregistered_strategy_version"
    if spec.get("strategy_version_isolated") is not True:
        return False, "strategy_version_isolation_not_true"
    sample = payload.get("sample")
    if not isinstance(sample, dict):
        return False, "sample_missing"
    required = {
        "ready_for_preregistered_effect_test",
        "reasons",
        "total_signals",
        "per_symbol",
        "minimum_total",
        "minimum_per_symbol",
    }
    if not required.issubset(sample):
        return False, "sample_contract_incomplete"
    if sample.get("minimum_total") != 60 or sample.get("minimum_per_symbol") != 10:
        return False, "sample_gate_mutated"

    coverage = payload.get("alignment_coverage")
    if not isinstance(coverage, dict):
        return False, "alignment_coverage_missing"
    coverage_required = {
        "status",
        "reason",
        "journal_signal_count",
        "aligned_signal_count",
        "unaligned_signal_count",
        "per_symbol",
    }
    if not coverage_required.issubset(coverage):
        return False, "alignment_coverage_contract_incomplete"
    journal_count = coverage.get("journal_signal_count")
    aligned_count = coverage.get("aligned_signal_count")
    unaligned_count = coverage.get("unaligned_signal_count")
    if not all(isinstance(value, int) and value >= 0 for value in (journal_count, aligned_count, unaligned_count)):
        return False, "alignment_coverage_counts_invalid"
    if journal_count != aligned_count + unaligned_count:
        return False, "alignment_coverage_counts_inconsistent"
    per_symbol = coverage.get("per_symbol")
    if not isinstance(per_symbol, dict):
        return False, "alignment_coverage_per_symbol_missing"
    for item in per_symbol.values():
        if not isinstance(item, dict):
            return False, "alignment_coverage_per_symbol_invalid"
        values = (
            item.get("journal_signals"),
            item.get("aligned_signals"),
            item.get("unaligned_signals"),
        )
        if not all(isinstance(value, int) and value >= 0 for value in values):
            return False, "alignment_coverage_per_symbol_counts_invalid"
        if item["journal_signals"] != item["aligned_signals"] + item["unaligned_signals"]:
            return False, "alignment_coverage_per_symbol_counts_inconsistent"

    status = coverage.get("status")
    reason = coverage.get("reason")
    if journal_count == 0:
        if status != "WAITING_FOR_FORWARD_SIGNALS" or reason != "waiting_for_forward_signals":
            return False, "waiting_state_invalid"
    elif unaligned_count > 0:
        if status != "COVERAGE_FAILURE" or reason != "alignment_coverage_failure":
            return False, "coverage_failure_state_invalid"
        if payload.get("ready_for_preregistered_effect_test") is not False:
            return False, "coverage_failure_did_not_close_effect_gate"
    elif status != "ALIGNED" or reason != "all_forward_signals_aligned":
        return False, "aligned_state_invalid"
    return True, "ok"


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required alignment status configuration is missing")
        return 1
    try:
        version = fetch_json(f"{base_url}/version", api_key)
        if version.get("commit_sha") != expected_sha:
            print("FAIL phase=version reason=expected_commit_not_deployed")
            return 1
        payload = fetch_json(
            f"{base_url}/v1/research/microstructure/alignment-status",
            api_key,
        )
    except HTTPError as exc:
        print(f"FAIL phase=alignment http_status={exc.code}")
        return 1
    except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL phase=alignment error_type={type(exc).__name__}")
        return 1

    ok, reason = validate_alignment_status(payload)
    spec = payload.get("spec") or {}
    safe = {
        "data_quality_ready": payload.get("data_quality_ready"),
        "alignment_coverage_ready": payload.get("alignment_coverage_ready"),
        "alignment_coverage": payload.get("alignment_coverage"),
        "ready_for_preregistered_effect_test": payload.get("ready_for_preregistered_effect_test"),
        "sample": payload.get("sample"),
        "interval": payload.get("interval"),
        "spec_version": spec.get("spec_version"),
        "preregistered_strategy_version": spec.get("preregistered_strategy_version"),
        "strategy_version_isolated": spec.get("strategy_version_isolated"),
        "promotion_allowed": payload.get("promotion_allowed"),
    }
    print("ALIGNMENT_STATUS=" + json.dumps(safe, sort_keys=True))
    if not ok:
        print(f"FAIL phase=alignment reason={reason}")
        return 1
    print("MICROSTRUCTURE ALIGNMENT SAMPLE STATUS VERIFIED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
