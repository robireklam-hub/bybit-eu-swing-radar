"""Outcome-blind feature adapter for controlled-pullback calibration v2.

The numerical feature construction is frozen from the v1 adapter. V2 changes
only research identity/strategy provenance so the v0.7.5 experiment remains
isolated. No journal labels, future returns, outcomes, ranking, eligibility or
execution state are read or mutated.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from research.microstructure import controlled_pullback_features_v1 as parent
from research.microstructure.controlled_pullback_v2 import (
    EXPERIMENT_ID,
    FEATURE_DATA_SPEC_VERSION,
    STRATEGY_VERSION,
    SYMBOLS,
)

FEATURE_ADAPTER_ID = "microstructure-controlled-pullback-feature-adapter-v2"
PARENT_FEATURE_ADAPTER_ID = parent.FEATURE_ADAPTER_ID
BUCKET_SECONDS = parent.BUCKET_SECONDS
MOMENTUM_LOOKBACK_SECONDS = parent.MOMENTUM_LOOKBACK_SECONDS
MOMENTUM_LOOKBACK_BUCKETS = parent.MOMENTUM_LOOKBACK_BUCKETS


def derive_calibration_feature_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    allowed_symbols: Iterable[str] = SYMBOLS,
) -> list[dict[str, Any]]:
    """Apply the frozen v1 feature transform to the isolated v2 symbol set."""
    return parent.derive_calibration_feature_rows(
        rows,
        allowed_symbols=allowed_symbols,
    )


def adapter_contract() -> dict[str, Any]:
    return {
        "feature_adapter_id": FEATURE_ADAPTER_ID,
        "parent_feature_adapter_id": PARENT_FEATURE_ADAPTER_ID,
        "experiment_id": EXPERIMENT_ID,
        "strategy_version": STRATEGY_VERSION,
        "feature_data_spec_version": FEATURE_DATA_SPEC_VERSION,
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "live_strategy_mutation": False,
        "bucket_seconds": BUCKET_SECONDS,
        "momentum_lookback_seconds": MOMENTUM_LOOKBACK_SECONDS,
        "gap_interpolation_allowed": False,
        "feature_transform_frozen_from_parent": True,
        "symbols": list(SYMBOLS),
    }
