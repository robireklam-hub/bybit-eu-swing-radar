"""Label-blind activation calibration for controlled-pullback research v2.

The threshold methodology is frozen from calibration v1. V2 changes only the
experiment/strategy provenance and requires the v2 feature adapter. Thresholds
are derived solely from pre-activation microstructure features; outcomes remain
forbidden and unknown fields fail closed.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Iterable

from research.microstructure import controlled_pullback_calibration_v1 as parent
from research.microstructure.controlled_pullback_features_v2 import FEATURE_ADAPTER_ID
from research.microstructure.controlled_pullback_v2 import (
    EXPERIMENT_ID,
    STRATEGY_VERSION,
    SYMBOLS,
)

CALIBRATION_ID = "microstructure-controlled-pullback-calibration-v2"
PARENT_CALIBRATION_ID = parent.CALIBRATION_ID
CALIBRATION_VERSION = "2.0.0"
MIN_ROWS_PER_SYMBOL = parent.MIN_ROWS_PER_SYMBOL

_ALLOWED_ROW_FIELDS = set(parent._ALLOWED_ROW_FIELDS)
_REQUIRED_NUMERIC_FIELDS = tuple(parent._REQUIRED_NUMERIC_FIELDS)
_PARENT_CONTRACT = parent.calibration_contract()

_CONTRACT = {
    "calibration_id": CALIBRATION_ID,
    "parent_calibration_id": PARENT_CALIBRATION_ID,
    "calibration_version": CALIBRATION_VERSION,
    "experiment_id": EXPERIMENT_ID,
    "strategy_version": STRATEGY_VERSION,
    "feature_adapter_id": FEATURE_ADAPTER_ID,
    "research_only": True,
    "label_blind": True,
    "outcomes_permitted": False,
    "activation_status": "CALIBRATION_RULE_FROZEN_NOT_ACTIVATED",
    "symbols": list(SYMBOLS),
    "source": "microstructure_buckets_pre_activation_only",
    "bucket_seconds": _PARENT_CONTRACT["bucket_seconds"],
    "minimum_rows_per_symbol": MIN_ROWS_PER_SYMBOL,
    "distribution_thresholds": deepcopy(_PARENT_CONTRACT["distribution_thresholds"]),
    "structural_thresholds": deepcopy(_PARENT_CONTRACT["structural_thresholds"]),
    "governance": deepcopy(_PARENT_CONTRACT["governance"]),
    "calibration_method_frozen_from_parent": True,
}


def calibration_contract() -> dict[str, Any]:
    return deepcopy(_CONTRACT)


def derive_thresholds(
    rows: Iterable[dict[str, Any]],
    *,
    calibration_until_utc: str | datetime,
    forward_start_utc: str | datetime,
) -> dict[str, Any]:
    """Derive one immutable v2 threshold snapshot from strictly pre-start features."""
    cutoff = parent._utc(calibration_until_utc)
    forward_start = parent._utc(forward_start_utc)
    if cutoff >= forward_start:
        raise ValueError("calibration cutoff must be strictly before forward start")

    by_symbol: dict[str, list[dict[str, float]]] = {symbol: [] for symbol in SYMBOLS}
    latest_row_at = None
    for raw in rows:
        unknown = set(raw) - _ALLOWED_ROW_FIELDS
        if unknown:
            raise ValueError(
                "calibration row contains non-feature fields: "
                + ", ".join(sorted(unknown))
            )
        symbol = str(raw.get("symbol", "")).upper()
        if symbol not in by_symbol:
            raise ValueError(f"unexpected calibration symbol: {symbol or '<missing>'}")
        bucket_start = parent._utc(raw.get("bucket_start"))
        if bucket_start >= cutoff or bucket_start >= forward_start:
            raise ValueError("calibration row is not strictly pre-activation")
        latest_row_at = (
            bucket_start if latest_row_at is None else max(latest_row_at, bucket_start)
        )
        by_symbol[symbol].append(
            {
                field: parent._finite_nonnegative(raw.get(field), field)
                for field in _REQUIRED_NUMERIC_FIELDS
            }
        )

    q = _CONTRACT["distribution_thresholds"]
    thresholds: dict[str, dict[str, float]] = {}
    counts: dict[str, int] = {}
    for symbol in SYMBOLS:
        sample = by_symbol[symbol]
        counts[symbol] = len(sample)
        if len(sample) < MIN_ROWS_PER_SYMBOL:
            raise ValueError(
                f"insufficient pre-activation calibration rows for {symbol}: "
                f"{len(sample)}<{MIN_ROWS_PER_SYMBOL}"
            )
        thresholds[symbol] = {
            "momentum_mid_return_60s_abs_min": parent._quantile(
                [row["mid_return_60s_abs"] for row in sample],
                q["momentum_mid_return_abs_quantile"],
            ),
            "momentum_aggressive_flow_share_abs_min": parent._quantile(
                [row["aggressive_flow_share_abs"] for row in sample],
                q["momentum_aggressive_flow_abs_quantile"],
            ),
            "reacceleration_aggressive_flow_share_abs_min": parent._quantile(
                [row["aggressive_flow_share_abs"] for row in sample],
                q["reacceleration_aggressive_flow_abs_quantile"],
            ),
            "reacceleration_book_pressure_abs_min": parent._quantile(
                [row["book_pressure_abs"] for row in sample],
                q["reacceleration_book_pressure_abs_quantile"],
            ),
        }

    return {
        "calibration_id": CALIBRATION_ID,
        "parent_calibration_id": PARENT_CALIBRATION_ID,
        "calibration_version": CALIBRATION_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "strategy_version": STRATEGY_VERSION,
        "feature_adapter_id": FEATURE_ADAPTER_ID,
        "research_only": True,
        "label_blind": True,
        "outcome_visible": False,
        "promotion_allowed": False,
        "calibration_until_utc": cutoff.isoformat(),
        "forward_start_utc": forward_start.isoformat(),
        "latest_calibration_row_utc": (
            latest_row_at.isoformat() if latest_row_at else None
        ),
        "sample_rows_per_symbol": counts,
        "thresholds_by_symbol": thresholds,
        "structural_thresholds": deepcopy(_CONTRACT["structural_thresholds"]),
        "threshold_recalibration_allowed": False,
        "calibration_method_frozen_from_parent": True,
        "live_strategy_mutation": False,
    }
