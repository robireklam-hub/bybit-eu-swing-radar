#!/usr/bin/env python3
"""Read-only production check for preregistered microstructure alignment sample readiness."""
from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
    safe = {
        "data_quality_ready": payload.get("data_quality_ready"),
        "ready_for_preregistered_effect_test": payload.get("ready_for_preregistered_effect_test"),
        "sample": payload.get("sample"),
        "interval": payload.get("interval"),
        "spec_version": (payload.get("spec") or {}).get("spec_version"),
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
