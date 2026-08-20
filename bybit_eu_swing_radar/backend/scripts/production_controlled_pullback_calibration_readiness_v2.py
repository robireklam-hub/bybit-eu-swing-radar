#!/usr/bin/env python3
"""Exact-SHA, outcome-blind activation guard for controlled-pullback research v2.

The v2 cohort is already activated from an immutable pre-outcome calibration
snapshot. This monitor must therefore validate that frozen activation contract;
it must never recalculate calibration readiness from rolling post-activation
microstructure buckets.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from research.microstructure.controlled_pullback_activation_v2 import (
    ACTIVATION_ID,
    CALIBRATION_UNTIL_UTC,
    FORWARD_START_UTC,
    SOURCE_MAIN_SHA,
    activation_contract_valid,
    activation_snapshot,
)
from research.microstructure.controlled_pullback_calibration_v2 import (
    CALIBRATION_ID,
    MIN_ROWS_PER_SYMBOL,
)
from research.microstructure.controlled_pullback_features_v2 import FEATURE_ADAPTER_ID
from research.microstructure.controlled_pullback_v2 import (
    EXPERIMENT_ID,
    STRATEGY_VERSION,
    SYMBOLS,
)


def fetch_json(url: str, api_key: str, timeout: float = 20.0) -> dict[str, Any]:
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "controlled-pullback-activation-guard-v2/1",
            "X-Radar-Key": api_key,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def summarize_activation(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    frozen = activation_snapshot() if snapshot is None else snapshot
    if not activation_contract_valid(frozen):
        raise ValueError("controlled-pullback v2 activation contract is invalid")

    counts = frozen.get("sample_rows_per_symbol")
    thresholds = frozen.get("thresholds_by_symbol")
    if not isinstance(counts, dict) or set(counts) != set(SYMBOLS):
        raise ValueError("activation sample symbols do not match preregistration")
    if not isinstance(thresholds, dict) or set(thresholds) != set(SYMBOLS):
        raise ValueError("activation threshold symbols do not match preregistration")
    if any(int(counts[symbol]) < MIN_ROWS_PER_SYMBOL for symbol in SYMBOLS):
        raise ValueError("frozen activation sample is below minimum calibration size")

    return {
        "research_only": True,
        "label_blind": True,
        "outcome_visible": False,
        "promotion_allowed": False,
        "live_strategy_mutation": False,
        "threshold_recalibration_allowed": False,
        "rolling_recalibration_performed": False,
        "activation_performed": True,
        "activation_id": ACTIVATION_ID,
        "experiment_id": EXPERIMENT_ID,
        "strategy_version": STRATEGY_VERSION,
        "feature_adapter_id": FEATURE_ADAPTER_ID,
        "calibration_id": CALIBRATION_ID,
        "activation_source_main_sha": SOURCE_MAIN_SHA,
        "calibration_until_utc": CALIBRATION_UNTIL_UTC,
        "forward_start_utc": FORWARD_START_UTC,
        "minimum_rows_per_symbol": MIN_ROWS_PER_SYMBOL,
        "frozen_sample_rows_per_symbol": {symbol: int(counts[symbol]) for symbol in SYMBOLS},
        "frozen_threshold_symbols": sorted(thresholds),
        "activation_contract_valid": True,
    }


def run(base_url: str, api_key: str, expected_sha: str) -> int:
    base = base_url.rstrip("/")
    try:
        version = fetch_json(f"{base}/version", api_key)
    except HTTPError as exc:
        print(f"FAIL phase=version http_status={exc.code}")
        return 1
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"FAIL phase=version error_type={type(exc).__name__}")
        return 1
    if version.get("commit_sha") != expected_sha:
        print("FAIL phase=version reason=expected_commit_not_deployed")
        return 1

    try:
        status = summarize_activation()
    except ValueError as exc:
        print(f"FAIL phase=activation reason={str(exc)[:500]}")
        return 1

    print(
        "CONTROLLED_PULLBACK_ACTIVATION_V2_STATUS="
        + json.dumps(status, sort_keys=True)
    )
    print("CONTROLLED-PULLBACK V2 IMMUTABLE ACTIVATION VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required controlled-pullback v2 activation configuration is missing")
        return 1
    return run(base_url, api_key, expected_sha)


if __name__ == "__main__":
    sys.exit(main())
