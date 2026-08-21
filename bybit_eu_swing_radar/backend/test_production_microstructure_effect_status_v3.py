from research.microstructure.effect_analysis_v3 import effect_analysis_spec
from scripts.production_microstructure_effect_status_v3 import validate_effect_status_v3


def _payload(status="WAITING_FOR_SAMPLE"):
    spec = effect_analysis_spec()
    payload = {
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "threshold_search_allowed": False,
        "model_search_allowed": False,
        "outcome_visible": False,
        "status": status,
        "effect_spec": spec,
    }
    if status in {"WAITING_FOR_CLOSED_OUTCOMES", "COMPLETE"}:
        payload["cohort_gate"] = {
            "cohort_frozen": True,
            "cohort_size": 60,
            "minimum_total": 60,
            "minimum_per_symbol": 10,
            "per_symbol": {"BTCUSDC": 27, "ETHUSDC": 11, "SOLUSDC": 22},
            "cohort_last_opened_at": "2026-08-21T08:55:00+00:00",
            "cohort_last_signal_id": 12345,
            "cohort_boundary_order": ["opened_at", "signal_id"],
        }
    return payload


def test_waiting_payload_is_fail_closed_and_valid():
    payload = _payload()
    assert payload["effect_spec"]["primary_outcome"] == "journal.net_r"
    assert validate_effect_status_v3(payload) == (True, "ok")


def test_validator_rejects_outcome_visibility_before_gate():
    payload = _payload()
    payload["outcome_visible"] = True
    assert validate_effect_status_v3(payload)[0] is False


def test_validator_rejects_stale_synthetic_outcome_alias():
    payload = _payload()
    payload["effect_spec"]["primary_outcome"] = "journal.net_r_after_costs"
    assert validate_effect_status_v3(payload) == (False, "primary_outcome_mutated")


def test_validator_rejects_outcome_semantics_drift():
    payload = _payload()
    payload["effect_spec"]["primary_outcome_semantics"] = "synthetic alias"
    assert validate_effect_status_v3(payload) == (False, "primary_outcome_semantics_mutated")


def test_waiting_for_closed_outcomes_requires_exact_frozen_partition():
    payload = _payload("WAITING_FOR_CLOSED_OUTCOMES")
    payload.update({"closed_outcomes": 54, "missing_outcomes": 6, "results": []})
    assert validate_effect_status_v3(payload) == (True, "ok")

    payload["missing_outcomes"] = 5
    assert validate_effect_status_v3(payload) == (False, "outcome_partition_mismatch")


def test_validator_rejects_mutated_frozen_cohort_symbol_partition():
    payload = _payload("WAITING_FOR_CLOSED_OUTCOMES")
    payload.update({"closed_outcomes": 54, "missing_outcomes": 6, "results": []})
    payload["cohort_gate"]["per_symbol"]["BTCUSDC"] = 26
    assert validate_effect_status_v3(payload) == (False, "cohort_symbol_partition_mismatch")


def test_validator_requires_exact_tuple_boundary_identity():
    payload = _payload("WAITING_FOR_CLOSED_OUTCOMES")
    payload.update({"closed_outcomes": 54, "missing_outcomes": 6, "results": []})
    del payload["cohort_gate"]["cohort_last_signal_id"]
    assert validate_effect_status_v3(payload) == (False, "cohort_boundary_signal_id_missing")

    payload = _payload("WAITING_FOR_CLOSED_OUTCOMES")
    payload.update({"closed_outcomes": 54, "missing_outcomes": 6, "results": []})
    payload["cohort_gate"]["cohort_boundary_order"] = ["signal_id", "opened_at"]
    assert validate_effect_status_v3(payload) == (False, "cohort_boundary_order_mutated")


def test_validator_rejects_results_before_all_outcomes_close():
    payload = _payload("WAITING_FOR_CLOSED_OUTCOMES")
    payload.update({"closed_outcomes": 54, "missing_outcomes": 6, "results": [{"id": "leak"}]})
    assert validate_effect_status_v3(payload) == (False, "results_visible_before_all_closed")


def test_validator_accepts_complete_frozen_hypothesis_set_only():
    payload = _payload("COMPLETE")
    payload["outcome_visible"] = True
    payload["closed_outcomes"] = 60
    payload["missing_outcomes"] = 0
    payload["promotion_decision"] = "NO_PROMOTION_REQUIRES_SUBSEQUENT_UNTOUCHED_VALIDATION"
    payload["results"] = [
        {"id": "H1_FLOW_BOOK_CONCORDANCE", "feature": "flow_book_concordance_60s", "expected_direction": "positive", "measured_effect_is_descriptive": True},
        {"id": "H2_MICROPRICE_DISPLACEMENT", "feature": "side_microprice_displacement_bps_15s", "expected_direction": "positive", "measured_effect_is_descriptive": True},
        {"id": "H3_BOOK_CHURN_PRESSURE", "feature": "side_book_pressure_ratio_60s", "expected_direction": "positive", "measured_effect_is_descriptive": True},
        {"id": "H4_SPREAD_COST", "feature": "spread_bps_mean_15s", "expected_direction": "negative", "measured_effect_is_descriptive": True},
    ]
    assert validate_effect_status_v3(payload) == (True, "ok")
    payload["results"][0]["expected_direction"] = "negative"
    assert validate_effect_status_v3(payload)[0] is False
