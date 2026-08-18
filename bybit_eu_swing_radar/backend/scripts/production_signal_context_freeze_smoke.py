"""Exact-SHA production smoke for Signal-Time Context Freeze v1."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

SPEC_VERSION = "signal-context-freeze-v1"


def _request(base: str, key: str, path: str, method: str = "GET") -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base.rstrip('/')}{path}",
        method=method,
        headers={"X-Radar-Key": key, "Accept": "application/json"},
    )
    if method == "POST":
        request.data = b""
        request.add_header("Content-Length", "0")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_exact_version(base: str, expected_sha: str) -> None:
    for _ in range(60):
        try:
            payload = _request(base, "unused", "/version")
            if payload.get("commit_sha") == expected_sha:
                return
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            pass
        time.sleep(3)
    raise AssertionError(f"production /version did not reach exact SHA {expected_sha}")


def validate_spec(payload: dict[str, Any]) -> tuple[bool, str]:
    if payload.get("version") != SPEC_VERSION:
        return False, "wrong_spec_version"
    required_true = ("research_only", "label_blind")
    if any(payload.get(field) is not True for field in required_true):
        return False, "research_contract_missing"
    required_false = (
        "outcome_fields_read",
        "live_strategy_mutated",
        "promotion_allowed",
        "execution_proof",
    )
    if any(payload.get(field) is not False for field in required_false):
        return False, "safety_contract_failed"
    return True, "ok"


def validate_capture(payload: dict[str, Any], expected_sha: str) -> tuple[bool, str]:
    if payload.get("spec_version") != SPEC_VERSION:
        return False, "wrong_capture_spec"
    if payload.get("source_commit_sha") != expected_sha:
        return False, "capture_sha_mismatch"
    if payload.get("research_only") is not True or payload.get("label_blind") is not True:
        return False, "capture_research_contract_failed"
    if payload.get("outcome_fields_read") is not False:
        return False, "capture_read_outcomes"
    if payload.get("live_strategy_mutated") is not False or payload.get("promotion_allowed") is not False:
        return False, "capture_mutation_or_promotion"
    examined = int(payload.get("signals_examined") or 0)
    inserted = int(payload.get("inserted") or 0)
    if examined < 0 or inserted < 0 or inserted > examined:
        return False, "invalid_capture_counts"
    symbols = list(payload.get("recorder_symbols") or [])
    if len(symbols) > 12 or any(not str(symbol).endswith("USDC") for symbol in symbols):
        return False, "invalid_recorder_symbols"
    return True, "ok"


def validate_status(payload: dict[str, Any], expected_sha: str) -> tuple[bool, str]:
    if payload.get("spec_version") != SPEC_VERSION:
        return False, "wrong_status_spec"
    if payload.get("source_commit_sha") != expected_sha:
        return False, "status_sha_mismatch"
    if payload.get("research_only") is not True or payload.get("label_blind") is not True:
        return False, "status_research_contract_failed"
    if payload.get("outcome_fields_read") is not False:
        return False, "status_read_outcomes"
    if payload.get("live_strategy_mutated") is not False or payload.get("promotion_allowed") is not False:
        return False, "status_mutation_or_promotion"
    journal = int(payload.get("journal_signal_count") or 0)
    frozen = int(payload.get("frozen_signal_count") or 0)
    if journal < 0 or frozen < 0 or frozen > journal:
        return False, "invalid_status_counts"
    gate = payload.get("future_effect_gate") or {}
    if "ready_for_future_effect_test" not in gate:
        return False, "missing_future_effect_gate"
    symbols = list(payload.get("recorder_symbols") or [])
    if len(symbols) > 12 or any(not str(symbol).endswith("USDC") for symbol in symbols):
        return False, "invalid_status_recorder_symbols"
    return True, "ok"


def main() -> None:
    base = os.environ["PRODUCTION_RADAR_API_BASE_URL"].rstrip("/")
    key = os.environ["PRODUCTION_RADAR_API_KEY"]
    expected_sha = os.environ["EXPECTED_SHA"]
    wait_for_exact_version(base, expected_sha)

    spec_payload = _request(base, key, "/v1/research/signal-context-freeze/spec")
    ok, reason = validate_spec(spec_payload)
    if not ok:
        raise AssertionError(reason)

    capture = _request(
        base, key, "/v1/research/signal-context-freeze/capture", method="POST"
    )
    ok, reason = validate_capture(capture, expected_sha)
    if not ok:
        raise AssertionError(reason)

    status = _request(base, key, "/v1/research/signal-context-freeze/status")
    ok, reason = validate_status(status, expected_sha)
    if not ok:
        raise AssertionError(reason)

    counts = status.get("counts") or {}
    gate = status.get("future_effect_gate") or {}
    print(
        "SIGNAL_CONTEXT_FREEZE="
        + json.dumps(
            {
                "source_commit_sha": status.get("source_commit_sha"),
                "journal_signal_count": status.get("journal_signal_count"),
                "frozen_signal_count": status.get("frozen_signal_count"),
                "freeze_coverage_pct": status.get("freeze_coverage_pct"),
                "long_count": counts.get("long_count"),
                "short_count": counts.get("short_count"),
                "distinct_utc_days": counts.get("distinct_utc_days"),
                "cross_layer_covered": counts.get("cross_layer_covered"),
                "microstructure_aligned": counts.get("microstructure_aligned"),
                "ready_for_future_effect_test": gate.get("ready_for_future_effect_test"),
                "gate_reasons": gate.get("reasons"),
                "recorder_symbols": status.get("recorder_symbols"),
            },
            sort_keys=True,
        )
    )
    print("SIGNAL-TIME CONTEXT FREEZE V1 VERIFIED.")


if __name__ == "__main__":
    main()
