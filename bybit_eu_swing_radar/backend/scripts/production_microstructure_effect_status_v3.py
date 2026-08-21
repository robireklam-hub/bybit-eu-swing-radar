#!/usr/bin/env python3
"""Read-only production check for the separately preregistered v0.7.5 effect gate."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from research.microstructure.effect_analysis_v3 import PRIMARY_OUTCOME, PRIMARY_OUTCOME_SEMANTICS, SPEC_VERSION

EXPECTED = {
    "H1_FLOW_BOOK_CONCORDANCE": ("flow_book_concordance_60s", "positive"),
    "H2_MICROPRICE_DISPLACEMENT": ("side_microprice_displacement_bps_15s", "positive"),
    "H3_BOOK_CHURN_PRESSURE": ("side_book_pressure_ratio_60s", "positive"),
    "H4_SPREAD_COST": ("spread_bps_mean_15s", "negative"),
}
ALLOWED = {"WAITING_FOR_DATA_QUALITY", "WAITING_FOR_SAMPLE", "WAITING_FOR_CLOSED_OUTCOMES", "COMPLETE"}
WANTED_SYMBOLS = {"BTCUSDC", "ETHUSDC", "SOLUSDC"}
BOUNDARY_ORDER = ["opened_at", "signal_id"]


def fetch_json(url: str, api_key: str, timeout: float = 15.0) -> dict[str, Any]:
    request = Request(url, method="GET", headers={"Accept": "application/json", "User-Agent": "bybit-eu-microstructure-effect-v3/1", "X-Radar-Key": api_key})
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _validate_frozen_cohort(payload: dict[str, Any]) -> tuple[bool, str]:
    gate = payload.get("cohort_gate")
    if not isinstance(gate, dict) or gate.get("cohort_frozen") is not True:
        return False, "cohort_not_frozen"
    if gate.get("cohort_size") != 60 or gate.get("minimum_total") != 60 or gate.get("minimum_per_symbol") != 10:
        return False, "cohort_gate_mutated"
    per_symbol = gate.get("per_symbol")
    if not isinstance(per_symbol, dict) or set(per_symbol) != WANTED_SYMBOLS:
        return False, "cohort_symbol_partition_invalid"
    if any(not isinstance(per_symbol[s], int) or per_symbol[s] < 10 for s in WANTED_SYMBOLS):
        return False, "cohort_symbol_minimum_invalid"
    if sum(per_symbol.values()) != 60:
        return False, "cohort_symbol_partition_mismatch"
    if not isinstance(gate.get("cohort_last_opened_at"), str) or not gate.get("cohort_last_opened_at"):
        return False, "cohort_boundary_timestamp_missing"
    boundary_signal_id = gate.get("cohort_last_signal_id")
    if not isinstance(boundary_signal_id, int) or isinstance(boundary_signal_id, bool) or boundary_signal_id <= 0:
        return False, "cohort_boundary_signal_id_missing"
    if gate.get("cohort_boundary_order") != BOUNDARY_ORDER:
        return False, "cohort_boundary_order_mutated"
    closed = payload.get("closed_outcomes")
    missing = payload.get("missing_outcomes", 0 if payload.get("status") == "COMPLETE" else None)
    if not isinstance(closed, int) or not isinstance(missing, int) or closed < 0 or missing < 0:
        return False, "outcome_partition_invalid"
    if closed + missing != 60:
        return False, "outcome_partition_mismatch"
    return True, "ok"


def validate_effect_status_v3(payload: dict[str, Any]) -> tuple[bool, str]:
    for field, expected in (("research_only", True), ("live_strategy_mutated", False), ("promotion_allowed", False), ("threshold_search_allowed", False), ("model_search_allowed", False)):
        if payload.get(field) is not expected:
            return False, f"unexpected_{field}"
    if payload.get("error") or payload.get("error_type"):
        return False, "effect_query_error"
    spec = payload.get("effect_spec")
    if not isinstance(spec, dict) or spec.get("spec_version") != SPEC_VERSION:
        return False, "unexpected_effect_spec"
    if spec.get("preregistered_strategy_version") != "0.7.5":
        return False, "unexpected_strategy_version"
    if spec.get("minimum_signal_sample") != {"total": 60, "per_symbol": 10}:
        return False, "sample_gate_mutated"
    if spec.get("primary_outcome") != PRIMARY_OUTCOME:
        return False, "primary_outcome_mutated"
    if spec.get("primary_outcome_semantics") != PRIMARY_OUTCOME_SEMANTICS:
        return False, "primary_outcome_semantics_mutated"
    status = payload.get("status")
    if status not in ALLOWED:
        return False, "unexpected_status"
    if status in {"WAITING_FOR_DATA_QUALITY", "WAITING_FOR_SAMPLE"}:
        if payload.get("outcome_visible") is not False:
            return False, "outcome_visible_before_gate"
        return True, "ok"
    ok, reason = _validate_frozen_cohort(payload)
    if not ok:
        return ok, reason
    if status == "WAITING_FOR_CLOSED_OUTCOMES":
        if payload.get("outcome_visible") is not False:
            return False, "outcome_visible_before_all_closed"
        if payload.get("results") not in ([], None):
            return False, "results_visible_before_all_closed"
        if payload.get("closed_outcomes") >= 60 or payload.get("missing_outcomes") <= 0:
            return False, "waiting_closed_partition_invalid"
        return True, "ok"
    if payload.get("outcome_visible") is not True:
        return False, "complete_outcome_not_visible"
    if payload.get("closed_outcomes") != 60 or payload.get("missing_outcomes", 0) != 0:
        return False, "complete_outcome_partition_invalid"
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != 4:
        return False, "complete_results_invalid"
    seen = set()
    for result in results:
        if not isinstance(result, dict):
            return False, "result_not_object"
        hypothesis_id = str(result.get("id") or "")
        expected = EXPECTED.get(hypothesis_id)
        if expected is None or hypothesis_id in seen:
            return False, "hypothesis_set_invalid"
        seen.add(hypothesis_id)
        if (result.get("feature"), result.get("expected_direction")) != expected:
            return False, "hypothesis_contract_mutated"
        if result.get("measured_effect_is_descriptive") is not True:
            return False, "effect_not_descriptive"
    if seen != set(EXPECTED):
        return False, "hypotheses_incomplete"
    if payload.get("promotion_decision") != "NO_PROMOTION_REQUIRES_SUBSEQUENT_UNTOUCHED_VALIDATION":
        return False, "promotion_decision_invalid"
    return True, "ok"


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required v3 effect status configuration is missing")
        return 1
    try:
        version = fetch_json(f"{base_url}/version", api_key)
        if version.get("commit_sha") != expected_sha:
            print("FAIL phase=version reason=expected_commit_not_deployed")
            return 1
        payload = fetch_json(f"{base_url}/v1/research/microstructure/effect-status-v3", api_key)
    except HTTPError as exc:
        print(f"FAIL phase=effect-v3 http_status={exc.code}")
        return 1
    except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL phase=effect-v3 error_type={type(exc).__name__}")
        return 1
    ok, reason = validate_effect_status_v3(payload)
    safe = {key: payload.get(key) for key in ("status", "ready_for_preregistered_effect_test", "sample", "cohort_gate", "closed_outcomes", "missing_outcomes", "results", "outcome_visible", "promotion_allowed", "promotion_decision")}
    print("EFFECT_STATUS_V3=" + json.dumps(safe, sort_keys=True))
    if not ok:
        print(f"FAIL phase=effect-v3 reason={reason}")
        return 1
    print("MICROSTRUCTURE V0.7.5 PREREGISTERED EFFECT STATUS VERIFIED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
