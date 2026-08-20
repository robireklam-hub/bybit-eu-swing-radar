"""Immutable preregistration for controlled-pullback research under day strategy v0.7.5.

V1 remains frozen under strategy v0.7.4. This v2 experiment inherits the same
predeclared feature sequence, hypothesis, comparator, outcome definitions and
sample gates, but its label-blind feature source is the isolated v0.7.5
microstructure-forward-alignment-v3 cohort.

Research-only. The forward cohort was activated only after a separate,
outcome-blind calibration snapshot was frozen. No live ranking, scores,
eligibility or execution are mutated.
"""
from __future__ import annotations

from copy import deepcopy

from research.microstructure import alignment_v3
from research.microstructure import controlled_pullback_v1 as v1

EXPERIMENT_ID = "microstructure-controlled-pullback-reacceleration-v2"
PARENT_EXPERIMENT_ID = v1.EXPERIMENT_ID
SPEC_VERSION = "2.0.0"
STRATEGY_VERSION = "0.7.5"
FEATURE_DATA_SPEC_VERSION = alignment_v3.SPEC_VERSION
SYMBOLS = v1.SYMBOLS
FORWARD_START_UTC = "2026-08-20T10:19:00+00:00"
ACTIVATION_ID = "microstructure-controlled-pullback-activation-v2"

_PARENT = v1.preregistration()

_PREREGISTRATION = {
    "experiment_id": EXPERIMENT_ID,
    "parent_experiment_id": PARENT_EXPERIMENT_ID,
    "spec_version": SPEC_VERSION,
    "research_only": True,
    "immutable_preregistration": True,
    "activation_status": "ACTIVATED_FORWARD_ONLY",
    "activation_id": ACTIVATION_ID,
    "forward_start_utc": FORWARD_START_UTC,
    "forward_start_rule": (
        "Forward eligibility begins only at the explicitly frozen UTC timestamp; "
        "historical/backfilled observations remain ineligible and the threshold "
        "snapshot may not be recalibrated on forward outcomes."
    ),
    "strategy_version": STRATEGY_VERSION,
    "strategy_version_isolated": True,
    "feature_data_spec_version": FEATURE_DATA_SPEC_VERSION,
    "feature_data_strategy_version": alignment_v3.PREREGISTERED_STRATEGY_VERSION,
    "feature_data_forward_start": alignment_v3.COHORT_START_AT.isoformat(),
    "symbols": list(SYMBOLS),
    "direction_symmetric": _PARENT["direction_symmetric"],
    "label_blind": True,
    "post_signal_data_used_for_features": False,
    "outcome_visible": False,
    "promotion_allowed": False,
    "mutate_scores": False,
    "mutate_ranking": False,
    "mutate_eligibility": False,
    "mutate_execution": False,
    "execution_constraints_unchanged": deepcopy(_PARENT["execution_constraints_unchanged"]),
    "feature_sequence": deepcopy(_PARENT["feature_sequence"]),
    "primary_hypothesis": _PARENT["primary_hypothesis"],
    "primary_comparator": _PARENT["primary_comparator"],
    "outcomes_after_trigger_only": deepcopy(_PARENT["outcomes_after_trigger_only"]),
    "development_gate": deepcopy(_PARENT["development_gate"]),
    "validation_gate": deepcopy(_PARENT["validation_gate"]),
    "covariates_to_report": deepcopy(_PARENT["covariates_to_report"]),
    "governance": deepcopy(_PARENT["governance"]),
    "inherited_design_frozen_from_parent": True,
    "threshold_search_allowed": False,
}


def preregistration() -> dict:
    """Return a defensive copy of the frozen v2 research specification."""
    return deepcopy(_PREREGISTRATION)


def activation_ready(spec: dict | None = None) -> bool:
    """Validate that activation stays forward-only and all safety gates remain closed."""
    candidate = _PREREGISTRATION if spec is None else spec
    return (
        bool(candidate.get("forward_start_utc"))
        and candidate.get("activation_status") == "ACTIVATED_FORWARD_ONLY"
        and candidate.get("activation_id") == ACTIVATION_ID
        and candidate.get("outcome_visible") is False
        and candidate.get("promotion_allowed") is False
        and candidate.get("strategy_version") == STRATEGY_VERSION
        and candidate.get("feature_data_spec_version") == FEATURE_DATA_SPEC_VERSION
        and candidate.get("threshold_search_allowed") is False
        and candidate.get("mutate_scores") is False
        and candidate.get("mutate_ranking") is False
        and candidate.get("mutate_eligibility") is False
        and candidate.get("mutate_execution") is False
    )
