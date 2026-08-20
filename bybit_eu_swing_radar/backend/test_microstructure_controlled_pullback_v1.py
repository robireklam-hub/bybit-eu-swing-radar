from research.microstructure.controlled_pullback_v1 import (
    EXPERIMENT_ID,
    STRATEGY_VERSION,
    activation_ready,
    preregistration,
)


def test_preregistration_is_research_only_and_fail_closed():
    spec = preregistration()
    assert spec["experiment_id"] == EXPERIMENT_ID
    assert spec["strategy_version"] == STRATEGY_VERSION == "0.7.4"
    assert spec["strategy_version_isolated"] is True
    assert spec["research_only"] is True
    assert spec["immutable_preregistration"] is True
    assert spec["activation_status"] == "PREREGISTERED_NOT_ACTIVATED"
    assert spec["forward_start_utc"] is None
    assert spec["label_blind"] is True
    assert spec["outcome_visible"] is False
    assert spec["promotion_allowed"] is False
    assert activation_ready(spec) is False


def test_preregistration_cannot_mutate_live_strategy_or_execution():
    spec = preregistration()
    assert spec["mutate_scores"] is False
    assert spec["mutate_ranking"] is False
    assert spec["mutate_eligibility"] is False
    assert spec["mutate_execution"] is False
    constraints = spec["execution_constraints_unchanged"]
    assert constraints == {
        "quote": "USDC",
        "long": "USDC_SPOT_ONLY",
        "short": "VERIFIED_BORROWABLE_USDC_SPOT_MARGIN_ONLY",
        "derivatives_execution": False,
    }


def test_feature_sequence_and_sample_gates_are_frozen_before_outcomes():
    spec = preregistration()
    assert [stage["stage"] for stage in spec["feature_sequence"]] == [
        "MOMENTUM",
        "CONTROLLED_PULLBACK",
        "ORDER_FLOW_REACCELERATION",
    ]
    assert spec["direction_symmetric"] is True
    assert spec["development_gate"] == {"total_events": 60, "minimum_per_symbol": 10}
    assert spec["validation_gate"] == {"untouched_events": 40}
    assert spec["governance"] == {
        "threshold_search_before_development_gate": False,
        "outcome_peeking_before_development_gate": False,
        "single_rule_freeze_before_validation": True,
        "validation_untouched": True,
    }


def test_preregistration_returns_defensive_copy():
    first = preregistration()
    first["promotion_allowed"] = True
    first["feature_sequence"][0]["stage"] = "MUTATED"
    second = preregistration()
    assert second["promotion_allowed"] is False
    assert second["feature_sequence"][0]["stage"] == "MOMENTUM"


def test_activation_requires_explicit_frozen_forward_start_and_hidden_outcomes():
    spec = preregistration()
    spec["forward_start_utc"] = "2026-08-21T00:00:00Z"
    assert activation_ready(spec) is True
    spec["outcome_visible"] = True
    assert activation_ready(spec) is False
