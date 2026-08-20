"""Label-blind activation calibration for controlled-pullback research v1.

Research-only. This module derives deterministic feature thresholds exclusively from
pre-activation microstructure features. It has no import path into live ranking,
scoring, eligibility, triggers, orders, or execution.
"""
from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable

from research.microstructure.controlled_pullback_v1 import EXPERIMENT_ID, STRATEGY_VERSION, SYMBOLS

CALIBRATION_ID = "microstructure-controlled-pullback-calibration-v1"
CALIBRATION_VERSION = "1.0.0"
MIN_ROWS_PER_SYMBOL = 100

# Only contemporaneous/pre-trigger market features are admissible. Outcome fields
# are intentionally absent and unknown keys fail closed.
_ALLOWED_ROW_FIELDS = {
    "symbol",
    "bucket_start",
    "mid_return_60s_abs",
    "aggressive_flow_share_abs",
    "book_pressure_abs",
}
_REQUIRED_NUMERIC_FIELDS = (
    "mid_return_60s_abs",
    "aggressive_flow_share_abs",
    "book_pressure_abs",
)

_CONTRACT = {
    "calibration_id": CALIBRATION_ID,
    "calibration_version": CALIBRATION_VERSION,
    "experiment_id": EXPERIMENT_ID,
    "strategy_version": STRATEGY_VERSION,
    "research_only": True,
    "label_blind": True,
    "outcomes_permitted": False,
    "activation_status": "CALIBRATION_RULE_FROZEN_NOT_ACTIVATED",
    "symbols": list(SYMBOLS),
    "source": "microstructure_buckets_pre_activation_only",
    "bucket_seconds": 5,
    "minimum_rows_per_symbol": MIN_ROWS_PER_SYMBOL,
    "distribution_thresholds": {
        "momentum_mid_return_abs_quantile": 0.75,
        "momentum_aggressive_flow_abs_quantile": 0.70,
        "reacceleration_aggressive_flow_abs_quantile": 0.65,
        "reacceleration_book_pressure_abs_quantile": 0.65,
    },
    "structural_thresholds": {
        "pullback_retracement_fraction_min": 0.20,
        "pullback_retracement_fraction_max": 0.60,
        "spread_ratio_to_pre_impulse_max": 1.10,
        "top5_depth_ratio_to_pre_impulse_min": 0.90,
        "opposite_structure_break_allowed": False,
    },
    "governance": {
        "calibration_must_end_before_forward_start": True,
        "historical_backfill_after_activation_allowed": False,
        "outcome_conditioned_threshold_search": False,
        "threshold_recalibration_after_activation": False,
        "live_strategy_mutation": False,
    },
}


def calibration_contract() -> dict[str, Any]:
    """Return a defensive copy of the frozen, outcome-blind calibration rule."""
    return deepcopy(_CONTRACT)


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp must be an ISO-8601 string or datetime")
    if result.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return result.astimezone(timezone.utc)


def _finite_nonnegative(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return result


def _quantile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("cannot derive a quantile from an empty sample")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def derive_thresholds(
    rows: Iterable[dict[str, Any]],
    *,
    calibration_until_utc: str | datetime,
    forward_start_utc: str | datetime,
) -> dict[str, Any]:
    """Derive one immutable activation threshold snapshot from pre-start features.

    The function deliberately rejects unknown fields so outcome-bearing rows cannot
    accidentally enter the calibration path. Each row must precede both the frozen
    calibration cutoff and the forward cohort start.
    """
    cutoff = _utc(calibration_until_utc)
    forward_start = _utc(forward_start_utc)
    if cutoff >= forward_start:
        raise ValueError("calibration cutoff must be strictly before forward start")

    by_symbol: dict[str, list[dict[str, float]]] = {symbol: [] for symbol in SYMBOLS}
    latest_row_at: datetime | None = None
    for raw in rows:
        unknown = set(raw) - _ALLOWED_ROW_FIELDS
        if unknown:
            raise ValueError("calibration row contains non-feature fields: " + ", ".join(sorted(unknown)))
        symbol = str(raw.get("symbol", "")).upper()
        if symbol not in by_symbol:
            raise ValueError(f"unexpected calibration symbol: {symbol or '<missing>'}")
        bucket_start = _utc(raw.get("bucket_start"))
        if bucket_start >= cutoff or bucket_start >= forward_start:
            raise ValueError("calibration row is not strictly pre-activation")
        latest_row_at = bucket_start if latest_row_at is None else max(latest_row_at, bucket_start)
        by_symbol[symbol].append(
            {field: _finite_nonnegative(raw.get(field), field) for field in _REQUIRED_NUMERIC_FIELDS}
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
            "momentum_mid_return_60s_abs_min": _quantile(
                [row["mid_return_60s_abs"] for row in sample],
                q["momentum_mid_return_abs_quantile"],
            ),
            "momentum_aggressive_flow_share_abs_min": _quantile(
                [row["aggressive_flow_share_abs"] for row in sample],
                q["momentum_aggressive_flow_abs_quantile"],
            ),
            "reacceleration_aggressive_flow_share_abs_min": _quantile(
                [row["aggressive_flow_share_abs"] for row in sample],
                q["reacceleration_aggressive_flow_abs_quantile"],
            ),
            "reacceleration_book_pressure_abs_min": _quantile(
                [row["book_pressure_abs"] for row in sample],
                q["reacceleration_book_pressure_abs_quantile"],
            ),
        }

    return {
        "calibration_id": CALIBRATION_ID,
        "calibration_version": CALIBRATION_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "strategy_version": STRATEGY_VERSION,
        "research_only": True,
        "label_blind": True,
        "outcome_visible": False,
        "promotion_allowed": False,
        "calibration_until_utc": cutoff.isoformat(),
        "forward_start_utc": forward_start.isoformat(),
        "latest_calibration_row_utc": latest_row_at.isoformat() if latest_row_at else None,
        "sample_rows_per_symbol": counts,
        "thresholds_by_symbol": thresholds,
        "structural_thresholds": deepcopy(_CONTRACT["structural_thresholds"]),
        "threshold_recalibration_allowed": False,
        "live_strategy_mutation": False,
    }
