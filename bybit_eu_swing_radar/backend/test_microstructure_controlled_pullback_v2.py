from research.microstructure import alignment_v3
from research.microstructure import controlled_pullback_v1 as v1
from research.microstructure import controlled_pullback_v2 as v2
from research.microstructure.controlled_pullback_activation_v2 import (
    ACTIVATION_ID,
    FORWARD_START_UTC,
    activation_contract_valid,
    activation_snapshot,
)
from research.microstructure.controlled_pullback_detector_v2 import _validate_calibration_snapshot


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


def test_v2_forward_activation_is_frozen_without_opening_outcomes_or_promotion():
    spec = v2.preregistration()
    assert spec["research_only"] is True
    assert spec["immutable_preregistration"] is True
    assert spec["activation_status"] == "ACTIVATED_FORWARD_ONLY"
    assert spec["activation_id"] == ACTIVATION_ID
    assert spec["forward_start_utc"] == FORWARD_START_UTC
    assert spec["label_blind"] is True
    assert spec["post_signal_data_used_for_features"] is False
    assert spec["outcome_visible"] is False
    assert spec["promotion_allowed"] is False
    assert spec["threshold_search_allowed"] is False
    assert v2.activation_ready(spec) is True


def test_v2_activation_snapshot_is_immutable_label_blind_and_detector_compatible():
    snapshot = activation_snapshot()
    assert activation_contract_valid(snapshot) is True
    assert snapshot["outcome_visible"] is False
    assert snapshot["promotion_allowed"] is False
    assert snapshot["live_strategy_mutation"] is False
    assert snapshot["threshold_recalibration_allowed"] is False
    assert snapshot["sample_rows_per_symbol"] == {
        "BTCUSDC": 479,
        "ETHUSDC": 167,
        "SOLUSDC": 123,
    }
    forward_start, thresholds, structural = _validate_calibration_snapshot(snapshot)
    assert forward_start.isoformat() == FORWARD_START_UTC
    assert set(thresholds) == {"BTCUSDC", "ETHUSDC", "SOLUSDC"}
    assert structural["pullback_retracement_fraction_min"] == 0.2
    assert structural["pullback_retracement_fraction_max"] == 0.6


def test_v2_activation_contract_fails_closed_on_recalibration_or_gate_mutation():
    snapshot = activation_snapshot()
    snapshot["threshold_recalibration_allowed"] = True
    assert activation_contract_valid(snapshot) is False

    snapshot = activation_snapshot()
    snapshot["outcome_visible"] = True
    assert activation_contract_valid(snapshot) is False

    snapshot = activation_snapshot()
    snapshot["sample_rows_per_symbol"]["SOLUSDC"] = 99
    assert activation_contract_valid(snapshot) is False


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
    candidate["feature_data_spec_version"] = "wrong"
    assert v2.activation_ready(candidate) is False
    candidate = v2.preregistration()
    candidate["mutate_eligibility"] = True
    assert v2.activation_ready(candidate) is False
