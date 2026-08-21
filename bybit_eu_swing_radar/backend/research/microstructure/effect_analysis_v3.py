"""Preregistered effect-analysis contract for the v0.7.5 microstructure cohort.

This module deliberately contains no database access and does not read outcomes.
Its purpose is to freeze the statistical analysis plan after the label-blind
sample gate became ready but before any outcome-bearing query is permitted.

Research-only: no live strategy/scoring/eligibility/execution mutation path.
"""
from __future__ import annotations

from typing import Any

from research.microstructure import alignment_v3

SPEC_VERSION = "microstructure-forward-effect-analysis-v3"
PARENT_ALIGNMENT_SPEC_VERSION = alignment_v3.SPEC_VERSION
PREREGISTERED_STRATEGY_VERSION = alignment_v3.PREREGISTERED_STRATEGY_VERSION
# Production journal schema stores the configured-cost-adjusted outcome in net_r.
PRIMARY_OUTCOME = "journal.net_r"
PRIMARY_OUTCOME_SEMANTICS = "cost-adjusted net R from day_trade_signal_journal.net_r"
MIN_SIGNAL_SAMPLE_TOTAL = alignment_v3.MIN_SIGNAL_SAMPLE_TOTAL
MIN_SIGNAL_SAMPLE_PER_SYMBOL = alignment_v3.MIN_SIGNAL_SAMPLE_PER_SYMBOL

# Frozen before opening outcome-bearing rows. The v1 hypotheses and feature
# directions are inherited exactly; no threshold/model search is authorized.
HYPOTHESES = tuple(dict(item) for item in alignment_v3.HYPOTHESES)

ANALYSIS_METHODS = (
    {
        "id": "PRIMARY_SPEARMAN",
        "description": (
            "For each preregistered continuous feature, compute Spearman rank "
            "correlation against cost-adjusted journal net R across the full "
            "v0.7.5 cohort."
        ),
        "two_sided": True,
        "directional_interpretation": True,
    },
    {
        "id": "SYMBOL_STRATIFIED_SPEARMAN",
        "description": (
            "Repeat the same Spearman association separately for BTCUSDC, "
            "ETHUSDC and SOLUSDC as a heterogeneity diagnostic; no symbol-specific "
            "threshold fitting is permitted."
        ),
        "two_sided": True,
        "directional_interpretation": True,
    },
    {
        "id": "SIGN_CONSISTENCY",
        "description": (
            "Report whether the observed association sign matches the frozen "
            "expected direction for each hypothesis in the pooled cohort and "
            "across symbols."
        ),
        "two_sided": False,
        "directional_interpretation": True,
    },
)


def effect_analysis_spec() -> dict[str, Any]:
    """Return the immutable pre-outcome analysis contract."""
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
        "execution_authorized": False,
        "spec_version": SPEC_VERSION,
        "parent_alignment_spec_version": PARENT_ALIGNMENT_SPEC_VERSION,
        "preregistered_strategy_version": PREREGISTERED_STRATEGY_VERSION,
        "strategy_version_isolated": True,
        "cohort_start_at": alignment_v3.COHORT_START_AT.isoformat(),
        "minimum_signal_sample": {
            "total": MIN_SIGNAL_SAMPLE_TOTAL,
            "per_symbol": MIN_SIGNAL_SAMPLE_PER_SYMBOL,
        },
        "primary_outcome": PRIMARY_OUTCOME,
        "primary_outcome_semantics": PRIMARY_OUTCOME_SEMANTICS,
        "hypotheses": [dict(item) for item in HYPOTHESES],
        "analysis_methods": [dict(item) for item in ANALYSIS_METHODS],
        "threshold_search_allowed": False,
        "model_search_allowed": False,
        "feature_selection_allowed": False,
        "hypothesis_rewriting_allowed": False,
        "outcome_visible_during_preregistration": False,
        "promotion_allowed": False,
        "multiple_testing_policy": (
            "Report every preregistered hypothesis result. Do not select or hide "
            "results by p-value. Treat p-values and confidence intervals as "
            "descriptive evidence, not as a production-promotion gate."
        ),
        "missing_outcome_policy": (
            "Exclude only signals whose preregistered primary outcome is genuinely "
            "unavailable; report the excluded signal IDs/count and never impute an "
            "outcome."
        ),
        "analysis_open_condition": (
            "Outcome-bearing analysis may start only after the v0.7.5 alignment "
            "sample gate is ready (>=60 total and >=10 per BTCUSDC/ETHUSDC/SOLUSDC) "
            "with zero alignment failures, and only in a later change after this "
            "contract is merged."
        ),
        "promotion_rule": (
            "No live promotion from this DEVELOPMENT analysis alone. Any discovered "
            "threshold/model requires a separately preregistered untouched validation "
            "cohort before production consideration."
        ),
    }


def validate_effect_preregistration(spec: dict[str, Any]) -> tuple[bool, str]:
    """Fail closed if any critical preregistered invariant drifts."""
    required_false = (
        "live_strategy_mutated",
        "production_eligibility_mutated",
        "execution_authorized",
        "threshold_search_allowed",
        "model_search_allowed",
        "feature_selection_allowed",
        "hypothesis_rewriting_allowed",
        "outcome_visible_during_preregistration",
        "promotion_allowed",
    )
    if spec.get("research_only") is not True:
        return False, "research_only_not_true"
    for field in required_false:
        if spec.get(field) is not False:
            return False, f"unexpected_{field}"
    if spec.get("spec_version") != SPEC_VERSION:
        return False, "unexpected_spec_version"
    if spec.get("parent_alignment_spec_version") != PARENT_ALIGNMENT_SPEC_VERSION:
        return False, "unexpected_parent_alignment_spec"
    if spec.get("preregistered_strategy_version") != PREREGISTERED_STRATEGY_VERSION:
        return False, "unexpected_strategy_version"
    if spec.get("primary_outcome") != PRIMARY_OUTCOME:
        return False, "unexpected_primary_outcome"
    if spec.get("primary_outcome_semantics") != PRIMARY_OUTCOME_SEMANTICS:
        return False, "unexpected_primary_outcome_semantics"
    if spec.get("minimum_signal_sample") != {
        "total": MIN_SIGNAL_SAMPLE_TOTAL,
        "per_symbol": MIN_SIGNAL_SAMPLE_PER_SYMBOL,
    }:
        return False, "sample_gate_mutated"
    if spec.get("hypotheses") != [dict(item) for item in HYPOTHESES]:
        return False, "hypotheses_mutated"
    if spec.get("analysis_methods") != [dict(item) for item in ANALYSIS_METHODS]:
        return False, "analysis_methods_mutated"
    return True, "ok"
