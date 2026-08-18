#!/usr/bin/env python3
"""Read-only production check for the preregistered microstructure effect gate."""
from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EXPECTED_HYPOTHESES = {
    "H1": ("flow_book_concordance_60s", "positive"),
    "H2": ("side_microprice_displacement_bps_15s", "positive"),
    "H3": ("side_book_pressure_ratio_60s", "positive"),
    "H4": ("spread_bps_mean_15s", "negative"),
}
ALLOWED_STATUSES = {
    "WAITING_FOR_DATA_QUALITY",
    "WAITING_FOR_SAMPLE",
    "WAITING_FOR_CLOSED_OUTCOMES",
    "COMPLETE",
}


def fetch_json(url: str, api_key: str, timeout: float = 15.0) -> dict[str, Any]:
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "bybit-eu-microstructure-effect-status/1",
            "X-Radar-Key": api_key,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def validate_effect_status(payload: dict[str, Any]) -> tuple[bool, str]:
    if payload.get("research_only") is not True:
        return False, "research_only_not_true"
    if payload.get("live_strategy_mutated") is not False:
        return False, "live_strategy_mutated_not_false"
    if payload.get("promotion_allowed") is not False:
        return False, "promotion_allowed_not_false"
    if payload.get("error") or payload.get("error_type"):
        return False, "effect_query_error"
    spec = payload.get("effect_spec")
    if not isinstance(spec, dict) or spec.get("effect_spec_version") != "microstructure-effect-test-v1":
        return False, "unexpected_effect_spec"
    if spec.get("promotion_rule") != "Never promote from this forward sample alone; require a subsequent untouched validation period.":
        return False, "promotion_rule_mutated"
    status = payload.get("status")
    if status not in ALLOWED_STATUSES:
        return False, "unexpected_effect_status"
    if status == "COMPLETE":
        results = payload.get("results")
        if not isinstance(results, list) or len(results) != 4:
            return False, "complete_results_invalid"
        seen: set[str] = set()
        for result in results:
            if not isinstance(result, dict):
                return False, "complete_result_not_object"
            hypothesis_id = str(result.get("id") or "")
            expected = EXPECTED_HYPOTHESES.get(hypothesis_id)
            if expected is None or hypothesis_id in seen:
                return False, "complete_hypotheses_invalid"
            seen.add(hypothesis_id)
            if (result.get("feature"), result.get("expected_direction")) != expected:
                return False, "complete_hypothesis_contract_mutated"
            if result.get("verdict") not in {"SUPPORTED", "INCONCLUSIVE"}:
                return False, "complete_verdict_invalid"
        if seen != set(EXPECTED_HYPOTHESES):
            return False, "complete_hypotheses_incomplete"
        if payload.get("promotion_decision") != "NO_PROMOTION_REQUIRES_SUBSEQUENT_UNTOUCHED_VALIDATION":
            return False, "complete_promotion_decision_invalid"
    return True, "ok"


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required effect status configuration is missing")
        return 1
    try:
        version = fetch_json(f"{base_url}/version", api_key)
        if version.get("commit_sha") != expected_sha:
            print("FAIL phase=version reason=expected_commit_not_deployed")
            return 1
        payload = fetch_json(f"{base_url}/v1/research/microstructure/effect-status", api_key)
    except HTTPError as exc:
        print(f"FAIL phase=effect http_status={exc.code}")
        return 1
    except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL phase=effect error_type={type(exc).__name__}")
        return 1

    ok, reason = validate_effect_status(payload)
    safe = {
        "status": payload.get("status"),
        "ready_for_preregistered_effect_test": payload.get("ready_for_preregistered_effect_test"),
        "sample": payload.get("sample"),
        "cohort_gate": payload.get("cohort_gate"),
        "closed_outcomes": payload.get("closed_outcomes"),
        "missing_outcomes": payload.get("missing_outcomes"),
        "results": payload.get("results"),
        "promotion_allowed": payload.get("promotion_allowed"),
        "promotion_decision": payload.get("promotion_decision"),
        "effect_spec_version": (payload.get("effect_spec") or {}).get("effect_spec_version"),
    }
    print("EFFECT_STATUS=" + json.dumps(safe, sort_keys=True))
    if not ok:
        print(f"FAIL phase=effect reason={reason}")
        return 1
    print("MICROSTRUCTURE PREREGISTERED EFFECT STATUS VERIFIED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
