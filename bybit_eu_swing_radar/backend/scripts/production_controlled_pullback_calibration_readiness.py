#!/usr/bin/env python3
"""Exact-SHA, outcome-blind readiness probe for controlled-pullback calibration v1."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# This script is invoked directly from backend/scripts by GitHub Actions. Python
# otherwise puts only the scripts directory on sys.path, which makes the sibling
# backend/research package unavailable. Bootstrap the backend root explicitly so
# direct production execution and test imports use the same modules.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from research.microstructure.controlled_pullback_calibration_v1 import MIN_ROWS_PER_SYMBOL
from research.microstructure.controlled_pullback_features_v1 import derive_calibration_feature_rows
from research.microstructure.controlled_pullback_v1 import SYMBOLS

LOOKBACK_MINUTES = 360
LIMIT = 1000


def fetch_json(url: str, api_key: str, timeout: float = 20.0) -> dict[str, Any]:
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "controlled-pullback-calibration-readiness/1",
            "X-Radar-Key": api_key,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def summarize_payloads(payloads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    first_eligible: dict[str, str | None] = {}
    last_eligible: dict[str, str | None] = {}
    for symbol in SYMBOLS:
        payload = payloads.get(symbol)
        if not isinstance(payload, dict):
            raise ValueError(f"missing payload for {symbol}")
        if payload.get("research_only") is not True:
            raise ValueError(f"research_only contract failed for {symbol}")
        if payload.get("label_blind") is not True or payload.get("outcome_fields_read") is not False:
            raise ValueError(f"label-blind contract failed for {symbol}")
        if payload.get("promotion_allowed") is not False:
            raise ValueError(f"promotion_allowed contract failed for {symbol}")
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise ValueError(f"rows missing for {symbol}")
        eligible = derive_calibration_feature_rows(rows, allowed_symbols=(symbol,))
        counts[symbol] = len(eligible)
        first_eligible[symbol] = eligible[0]["bucket_start"] if eligible else None
        last_eligible[symbol] = eligible[-1]["bucket_start"] if eligible else None
    missing = [symbol for symbol in SYMBOLS if counts[symbol] < MIN_ROWS_PER_SYMBOL]
    return {
        "research_only": True,
        "label_blind": True,
        "outcome_visible": False,
        "promotion_allowed": False,
        "live_strategy_mutation": False,
        "lookback_minutes": LOOKBACK_MINUTES,
        "limit_per_symbol": LIMIT,
        "minimum_rows_per_symbol": MIN_ROWS_PER_SYMBOL,
        "eligible_rows_per_symbol": counts,
        "first_eligible_bucket": first_eligible,
        "last_eligible_bucket": last_eligible,
        "missing_sample_symbols": missing,
        "calibration_sample_ready": not missing,
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

    payloads: dict[str, dict[str, Any]] = {}
    for symbol in SYMBOLS:
        query = urlencode({"symbol": symbol, "lookback_minutes": LOOKBACK_MINUTES, "limit": LIMIT})
        try:
            payloads[symbol] = fetch_json(f"{base}/v1/research/microstructure/buckets?{query}", api_key)
        except HTTPError as exc:
            print(f"FAIL phase=buckets symbol={symbol} http_status={exc.code}")
            return 1
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"FAIL phase=buckets symbol={symbol} error_type={type(exc).__name__}")
            return 1

    try:
        status = summarize_payloads(payloads)
    except ValueError as exc:
        print(f"FAIL phase=readiness reason={str(exc)[:500]}")
        return 1

    print("CONTROLLED_PULLBACK_CALIBRATION_READINESS=" + json.dumps(status, sort_keys=True))
    if status["calibration_sample_ready"]:
        print("CONTROLLED-PULLBACK CALIBRATION SAMPLE READY.")
    else:
        print("CONTROLLED-PULLBACK CALIBRATION SAMPLE PENDING.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required calibration readiness configuration is missing")
        return 1
    return run(base_url, api_key, expected_sha)


if __name__ == "__main__":
    sys.exit(main())
