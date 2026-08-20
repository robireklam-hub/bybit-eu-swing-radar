from research.microstructure import alignment_v3
from research.microstructure import controlled_pullback_v1 as v1
from research.microstructure import controlled_pullback_v2 as v2


def test_v1_remains_frozen_while_v2_isolated_to_v075_alignment_v3():
    old = v1.preregistration()
    new = v2.preregistration()

    assert old["experiment_id"] == "microstructure-controlled-pullback-reacceleration-v1"
    assert old["strategy_version"] == "0.7.4"
    assert old["forward_start_utc"] is None

    assert new["experiment_id"] == "microstructure-controlled-pullback-reacceleration-v2"
    assert new["parent_experiment_id"] == v1.EXPERIMENT_ID
    assert new["strategy_version"] == "0.7.5"
    assert new["feature_data_spec_version"] == alignment_v3.SPEC_VERSION
    assert new["feature_data_strategy_version"] == "0.7.5"
    assert new["feature_data_forward_start"] == alignment_v3.COHORT_START_AT.isoformat()
    assert new["strategy_version_isolated"] is True


def test_v2_is_preregistered_but_deliberately_not_activated():
    spec = v2.preregistration()
    assert spec["research_only"] is True
    assert spec["immutable_preregistration"] is True
    assert spec["activation_status"] == "PREREGISTERED_NOT_ACTIVATED"
    assert spec["forward_start_utc"] is None
    assert spec["label_blind"] is True
    assert spec["post_signal_data_used_for_features"] is False
    assert spec["outcome_visible"] is False
    assert spec["promotion_allowed"] is False
    assert spec["threshold_search_allowed"] is False
    assert v2.activation_ready(spec) is False


def test_v2_inherits_frozen_design_but_not_v1_strategy_identity():
    old = v1.preregistration()
    new = v2.preregistration()
    for key in (
        "direction_symmetric",
        "feature_sequence",
        "primary_hypothesis",
        "primary_comparator",
        "outcomes_after_trigger_only",
        "development_gate",
        "validation_gate",
        "covariates_to_report",
        "governance",
        "execution_constraints_unchanged",
    ):
        assert new[key] == old[key], key
    assert new["inherited_design_frozen_from_parent"] is True
    assert new["strategy_version"] != old["strategy_version"]


def test_v2_cannot_mutate_live_strategy_or_execution():
    spec = v2.preregistration()
    assert spec["mutate_scores"] is False
    assert spec["mutate_ranking"] is False
    assert spec["mutate_eligibility"] is False
    assert spec["mutate_execution"] is False
    assert spec["execution_constraints_unchanged"] == {
        "quote": "USDC",
        "long": "USDC_SPOT_ONLY",
        "short": "VERIFIED_BORROWABLE_USDC_SPOT_MARGIN_ONLY",
        "derivatives_execution": False,
    }


def test_v2_returns_defensive_copy_and_activation_fails_closed_on_contract_mutation():
    first = v2.preregistration()
    first["feature_sequence"][0]["stage"] = "MUTATED"
    first["promotion_allowed"] = True
    second = v2.preregistration()
    assert second["feature_sequence"][0]["stage"] == "MOMENTUM"
    assert second["promotion_allowed"] is False

    candidate = v2.preregistration()
    candidate["forward_start_utc"] = "2026-08-21T00:00:00Z"
    assert v2.activation_ready(candidate) is True
    candidate["feature_data_spec_version"] = "wrong"
    assert v2.activation_ready(candidate) is False
