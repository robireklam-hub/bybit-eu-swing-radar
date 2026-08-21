from scripts.production_microstructure_effect_status_v3 import validate_effect_status_v3


def _payload(status="WAITING_FOR_SAMPLE"):
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "threshold_search_allowed": False,
        "model_search_allowed": False,
        "outcome_visible": False,
        "status": status,
        "effect_spec": {
            "spec_version": "microstructure-forward-effect-analysis-v3",
            "preregistered_strategy_version": "0.7.5",
            "minimum_signal_sample": {"total": 60, "per_symbol": 10},
            "primary_outcome": "journal.net_r_after_costs",
        },
    }


def test_waiting_payload_is_fail_closed_and_valid():
    assert validate_effect_status_v3(_payload()) == (True, "ok")


def test_validator_rejects_outcome_visibility_before_gate():
    payload = _payload()
    payload["outcome_visible"] = True
    assert validate_effect_status_v3(payload)[0] is False


def test_validator_accepts_complete_frozen_hypothesis_set_only():
    payload = _payload("COMPLETE")
    payload["outcome_visible"] = True
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
