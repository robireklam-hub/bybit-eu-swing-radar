"""Immutable preregistration for the controlled-pullback microstructure experiment.

Research-only. This module must not mutate live ranking, eligibility, scores, or execution.
The forward cohort is intentionally not activated here: its UTC start must be frozen in a
separate activation change before any outcome inspection for this experiment.
"""

from __future__ import annotations

from copy import deepcopy

EXPERIMENT_ID = "microstructure-controlled-pullback-reacceleration-v1"
SPEC_VERSION = "1.0.0"
STRATEGY_VERSION = "0.7.4"
SYMBOLS = ("BTCUSDC", "ETHUSDC", "SOLUSDC")

_PREREGISTRATION = {
    "experiment_id": EXPERIMENT_ID,
    "spec_version": SPEC_VERSION,
    "research_only": True,
    "immutable_preregistration": True,
    "activation_status": "PREREGISTERED_NOT_ACTIVATED",
    "forward_start_utc": None,
    "forward_start_rule": (
        "Freeze an explicit UTC timestamp in a follow-on activation PR before reading "
        "any outcomes for this experiment; historical/backfilled rows are ineligible."
    ),
    "strategy_version": STRATEGY_VERSION,
    "strategy_version_isolated": True,
    "symbols": list(SYMBOLS),
    "direction_symmetric": True,
    "label_blind": True,
    "outcome_visible": False,
    "promotion_allowed": False,
    "mutate_scores": False,
    "mutate_ranking": False,
    "mutate_eligibility": False,
    "mutate_execution": False,
    "execution_constraints_unchanged": {
        "quote": "USDC",
        "long": "USDC_SPOT_ONLY",
        "short": "VERIFIED_BORROWABLE_USDC_SPOT_MARGIN_ONLY",
        "derivatives_execution": False,
    },
    "feature_sequence": [
        {
            "stage": "MOMENTUM",
            "definition": (
                "Direction-normalized price displacement and signed aggressive-flow imbalance "
                "must agree using only observations available before the candidate event."
            ),
        },
        {
            "stage": "CONTROLLED_PULLBACK",
            "definition": (
                "Price retraces part of the momentum leg without an opposite structural break; "
                "spread/depth quality must not be degraded relative to the pre-impulse baseline."
            ),
        },
        {
            "stage": "ORDER_FLOW_REACCELERATION",
            "definition": (
                "Signed aggressive-flow and book-pressure measures realign with the original "
                "momentum direction before the research trigger timestamp."
            ),
        },
    ],
    "primary_hypothesis": (
        "Controlled pullbacks with pre-trigger order-flow reacceleration have superior "
        "direction-normalized forward performance versus the frozen momentum-only comparator."
    ),
    "primary_comparator": "MOMENTUM_ONLY_SAME_DIRECTION_SAME_SYMBOL",
    "outcomes_after_trigger_only": [
        "direction_normalized_return_5m",
        "direction_normalized_return_15m",
        "mae_15m",
        "mfe_15m",
    ],
    "development_gate": {"total_events": 60, "minimum_per_symbol": 10},
    "validation_gate": {"untouched_events": 40},
    "covariates_to_report": [
        "symbol",
        "direction",
        "session",
        "volatility_regime",
        "spread",
        "top_of_book_depth",
        "trade_volume",
    ],
    "governance": {
        "threshold_search_before_development_gate": False,
        "outcome_peeking_before_development_gate": False,
        "single_rule_freeze_before_validation": True,
        "validation_untouched": True,
    },
}


def preregistration() -> dict:
    """Return a defensive copy of the frozen research specification."""
    return deepcopy(_PREREGISTRATION)


def activation_ready(spec: dict | None = None) -> bool:
    """Fail closed until an explicit forward UTC start is frozen later."""
    candidate = _PREREGISTRATION if spec is None else spec
    return bool(candidate.get("forward_start_utc")) and candidate.get("outcome_visible") is False
