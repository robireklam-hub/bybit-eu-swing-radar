from research.microstructure import alignment_v3
from research.microstructure.effect_analysis_v3 import (
    ANALYSIS_METHODS,
    HYPOTHESES,
    MIN_SIGNAL_SAMPLE_PER_SYMBOL,
    MIN_SIGNAL_SAMPLE_TOTAL,
    PARENT_ALIGNMENT_SPEC_VERSION,
    PREREGISTERED_STRATEGY_VERSION,
    PRIMARY_OUTCOME,
    PRIMARY_OUTCOME_SEMANTICS,
    SPEC_VERSION,
    effect_analysis_spec,
    validate_effect_preregistration,
)


def test_effect_preregistration_inherits_frozen_v3_contract() -> None:
    spec = effect_analysis_spec()
    assert spec["spec_version"] == SPEC_VERSION
    assert spec["parent_alignment_spec_version"] == alignment_v3.SPEC_VERSION
    assert PARENT_ALIGNMENT_SPEC_VERSION == alignment_v3.SPEC_VERSION
    assert spec["preregistered_strategy_version"] == "0.7.5"
    assert PREREGISTERED_STRATEGY_VERSION == alignment_v3.PREREGISTERED_STRATEGY_VERSION
    assert spec["cohort_start_at"] == alignment_v3.COHORT_START_AT.isoformat()
    assert spec["minimum_signal_sample"] == {"total": 60, "per_symbol": 10}
    assert MIN_SIGNAL_SAMPLE_TOTAL == alignment_v3.MIN_SIGNAL_SAMPLE_TOTAL
    assert MIN_SIGNAL_SAMPLE_PER_SYMBOL == alignment_v3.MIN_SIGNAL_SAMPLE_PER_SYMBOL
    assert spec["primary_outcome"] == PRIMARY_OUTCOME == "journal.net_r"
    assert spec["primary_outcome_semantics"] == PRIMARY_OUTCOME_SEMANTICS
    assert PRIMARY_OUTCOME_SEMANTICS == "cost-adjusted net R from day_trade_signal_journal.net_r"
    assert spec["hypotheses"] == [dict(item) for item in alignment_v3.HYPOTHESES]
    assert tuple(spec["analysis_methods"]) == ANALYSIS_METHODS


def test_effect_preregistration_is_pre_outcome_and_non_mutating() -> None:
    spec = effect_analysis_spec()
    assert spec["research_only"] is True
    assert spec["live_strategy_mutated"] is False
    assert spec["production_eligibility_mutated"] is False
    assert spec["execution_authorized"] is False
    assert spec["threshold_search_allowed"] is False
    assert spec["model_search_allowed"] is False
    assert spec["feature_selection_allowed"] is False
    assert spec["hypothesis_rewriting_allowed"] is False
    assert spec["outcome_visible_during_preregistration"] is False
    assert spec["promotion_allowed"] is False
    ok, reason = validate_effect_preregistration(spec)
    assert ok is True
    assert reason == "ok"


def test_effect_preregistration_fails_closed_on_threshold_or_promotion_drift() -> None:
    for field in ("threshold_search_allowed", "model_search_allowed", "promotion_allowed"):
        spec = effect_analysis_spec()
        spec[field] = True
        ok, reason = validate_effect_preregistration(spec)
        assert ok is False
        assert reason == f"unexpected_{field}"


def test_effect_preregistration_fails_closed_on_hypothesis_or_method_drift() -> None:
    spec = effect_analysis_spec()
    spec["hypotheses"] = spec["hypotheses"][:-1]
    assert validate_effect_preregistration(spec) == (False, "hypotheses_mutated")

    spec = effect_analysis_spec()
    spec["analysis_methods"] = spec["analysis_methods"][:-1]
    assert validate_effect_preregistration(spec) == (False, "analysis_methods_mutated")


def test_effect_preregistration_fails_closed_on_outcome_contract_drift() -> None:
    spec = effect_analysis_spec()
    spec["primary_outcome"] = "journal.net_r_after_costs"
    assert validate_effect_preregistration(spec) == (False, "unexpected_primary_outcome")

    spec = effect_analysis_spec()
    spec["primary_outcome_semantics"] = "synthetic alias"
    assert validate_effect_preregistration(spec) == (
        False,
        "unexpected_primary_outcome_semantics",
    )


def test_effect_preregistration_keeps_all_four_preregistered_hypotheses() -> None:
    assert [item["id"] for item in HYPOTHESES] == [
        "H1_FLOW_BOOK_CONCORDANCE",
        "H2_MICROPRICE_DISPLACEMENT",
        "H3_BOOK_CHURN_PRESSURE",
        "H4_SPREAD_COST",
    ]
    assert [item["expected_direction"] for item in HYPOTHESES] == [
        "positive",
        "positive",
        "positive",
        "negative",
    ]
