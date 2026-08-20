"""Immutable forward activation snapshot for controlled-pullback research v2.

Generated once from exact production main using label-blind pre-activation
microstructure buckets. No recalibration, outcome inspection, promotion, or live
strategy mutation is permitted by this module.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from research.microstructure.controlled_pullback_calibration_v2 import CALIBRATION_ID
from research.microstructure.controlled_pullback_features_v2 import FEATURE_ADAPTER_ID
from research.microstructure.controlled_pullback_v2 import EXPERIMENT_ID, STRATEGY_VERSION

ACTIVATION_ID = "microstructure-controlled-pullback-activation-v2"
SOURCE_MAIN_SHA = "72c19b75d50b60f5259722f94bd08aa639c2b16d"
GENERATED_AT_UTC = "2026-08-20T09:49:51.228331+00:00"
CALIBRATION_UNTIL_UTC = "2026-08-20T09:49:00+00:00"
FORWARD_START_UTC = "2026-08-20T10:19:00+00:00"

_SNAPSHOT = {
    "activation_id": ACTIVATION_ID,
    "source_main_sha": SOURCE_MAIN_SHA,
    "generated_at_utc": GENERATED_AT_UTC,
    "calibration_id": CALIBRATION_ID,
    "experiment_id": EXPERIMENT_ID,
    "strategy_version": STRATEGY_VERSION,
    "feature_adapter_id": FEATURE_ADAPTER_ID,
    "research_only": True,
    "label_blind": True,
    "outcome_visible": False,
    "promotion_allowed": False,
    "live_strategy_mutation": False,
    "threshold_recalibration_allowed": False,
    "calibration_until_utc": CALIBRATION_UNTIL_UTC,
    "forward_start_utc": FORWARD_START_UTC,
    "latest_calibration_row_utc": "2026-08-20T09:48:40+00:00",
    "sample_rows_per_symbol": {
        "BTCUSDC": 479,
        "ETHUSDC": 167,
        "SOLUSDC": 123,
    },
    "thresholds_by_symbol": {
        "BTCUSDC": {
            "momentum_aggressive_flow_share_abs_min": 1.0,
            "momentum_mid_return_60s_abs_min": 0.001304469279169107,
            "reacceleration_aggressive_flow_share_abs_min": 1.0,
            "reacceleration_book_pressure_abs_min": 0.016575266534417623,
        },
        "ETHUSDC": {
            "momentum_aggressive_flow_share_abs_min": 1.0,
            "momentum_mid_return_60s_abs_min": 0.001563365571362052,
            "reacceleration_aggressive_flow_share_abs_min": 1.0,
            "reacceleration_book_pressure_abs_min": 0.02050293642318781,
        },
        "SOLUSDC": {
            "momentum_aggressive_flow_share_abs_min": 1.0,
            "momentum_mid_return_60s_abs_min": 0.0013999657710974667,
            "reacceleration_aggressive_flow_share_abs_min": 1.0,
            "reacceleration_book_pressure_abs_min": 0.014657674905557158,
        },
    },
    "structural_thresholds": {
        "opposite_structure_break_allowed": False,
        "pullback_retracement_fraction_max": 0.6,
        "pullback_retracement_fraction_min": 0.2,
        "spread_ratio_to_pre_impulse_max": 1.1,
        "top5_depth_ratio_to_pre_impulse_min": 0.9,
    },
}


def activation_snapshot() -> dict[str, Any]:
    return deepcopy(_SNAPSHOT)


def activation_contract_valid(snapshot: dict[str, Any] | None = None) -> bool:
    candidate = _SNAPSHOT if snapshot is None else snapshot
    try:
        cutoff = datetime.fromisoformat(str(candidate["calibration_until_utc"]).replace("Z", "+00:00"))
        start = datetime.fromisoformat(str(candidate["forward_start_utc"]).replace("Z", "+00:00"))
        latest = datetime.fromisoformat(str(candidate["latest_calibration_row_utc"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return False
    return (
        candidate.get("activation_id") == ACTIVATION_ID
        and candidate.get("calibration_id") == CALIBRATION_ID
        and candidate.get("experiment_id") == EXPERIMENT_ID
        and candidate.get("strategy_version") == STRATEGY_VERSION
        and candidate.get("feature_adapter_id") == FEATURE_ADAPTER_ID
        and candidate.get("research_only") is True
        and candidate.get("label_blind") is True
        and candidate.get("outcome_visible") is False
        and candidate.get("promotion_allowed") is False
        and candidate.get("live_strategy_mutation") is False
        and candidate.get("threshold_recalibration_allowed") is False
        and latest < cutoff < start
        and all(int(v) >= 100 for v in candidate.get("sample_rows_per_symbol", {}).values())
        and set(candidate.get("thresholds_by_symbol", {})) == {"BTCUSDC", "ETHUSDC", "SOLUSDC"}
    )
