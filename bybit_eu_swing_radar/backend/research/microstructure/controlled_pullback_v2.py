"""Immutable preregistration for controlled-pullback research under day strategy v0.7.5.

V1 remains frozen under strategy v0.7.4. This v2 experiment inherits the same
predeclared feature sequence, hypothesis, comparator, outcome definitions and
sample gates, but its label-blind feature source is the isolated v0.7.5
microstructure-forward-alignment-v3 cohort.

Research-only. The forward outcome cohort is intentionally NOT activated here.
A separate activation change must freeze a new UTC start before any v2 outcome
inspection. No live ranking, scores, eligibility or execution are mutated.
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

_PARENT = v1.preregistration()

_PREREGISTRATION = {
    "experiment_id": EXPERIMENT_ID,
    "parent_experiment_id": PARENT_EXPERIMENT_ID,
    "spec_version": SPEC_VERSION,
    "research_only": True,
    "immutable_preregistration": True,
    "activation_status": "PREREGISTERED_NOT_ACTIVATED",
    "forward_start_utc": None,
    "forward_start_rule": (
        "Freeze an explicit UTC timestamp in a separate v2 activation PR after this "
        "preregistration exists; do not inspect v2 outcomes before that timestamp. "
        "Historical/backfilled observations are ineligible."
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
    """Fail closed until a later change freezes a v2 forward UTC start."""
    candidate = _PREREGISTRATION if spec is None else spec
    return (
        bool(candidate.get("forward_start_utc"))
        and candidate.get("outcome_visible") is False
        and candidate.get("promotion_allowed") is False
        and candidate.get("strategy_version") == STRATEGY_VERSION
        and candidate.get("feature_data_spec_version") == FEATURE_DATA_SPEC_VERSION
    )
