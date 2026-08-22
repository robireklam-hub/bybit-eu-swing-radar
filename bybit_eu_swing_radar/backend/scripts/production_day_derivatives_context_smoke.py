#!/usr/bin/env python3
"""Fail-closed production smoke for day OI/funding priority and fallback."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CORE_SYMBOLS = ("BTCUSDC", "ETHUSDC", "SOLUSDC")
MAX_POLLS = 80
POLL_INTERVAL_SECONDS = 15


def fetch_json(url: str, api_key: str, timeout: float) -> dict[str, Any]:
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "bybit-eu-day-derivatives-context-smoke/1",
            "X-Radar-Key": api_key,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _get(
    fetch: Callable[[str, str, float], dict[str, Any]],
    base_url: str,
    path: str,
    api_key: str,
    timeout: float,
) -> dict[str, Any]:
    return fetch(f"{base_url.rstrip('/')}{path}", api_key, timeout)


def setup_context_usable(payload: dict[str, Any]) -> bool:
    derivatives = payload.get("derivatives")
    if not isinstance(derivatives, dict) or not derivatives:
        return False
    return any(
        derivatives.get(field) is not None
        for field in ("open_interest_usd", "funding_rate")
    )


def flow_context_usable(payload: dict[str, Any]) -> bool:
    if payload.get("data_quality") == "DEGRADED":
        return False
    coverage = str(payload.get("coverage_status") or "")
    if coverage in {
        "STALE_FLOW_CONTEXT",
        "STALE_SPOT_CONTEXT",
        "NO_BYBIT_GLOBAL_LINEAR_PERPETUAL_MATCH",
    }:
        return False
    derivatives = payload.get("bybit_global_derivatives")
    if not isinstance(derivatives, dict) or not derivatives:
        return False
    return any(
        derivatives.get(field) is not None
        for field in (
            "open_interest_size",
            "open_interest_value_quote",
            "funding_rate_decimal",
        )
    )


def evaluate(
    status: dict[str, Any],
    setups: dict[str, dict[str, Any]],
    flows: dict[str, dict[str, Any]],
    expected_sha: str,
) -> list[str]:
    failures: list[str] = []
    worker = status.get("worker")
    if not isinstance(worker, dict):
        return ["day status worker missing"]
    if worker.get("source_commit_sha") != expected_sha:
        failures.append("day worker source_commit_sha mismatch")

    priority = worker.get("coinalyze_priority_symbols")
    targeted = worker.get("coinalyze_priority_targeted_symbols")
    missing_analysis = worker.get("coinalyze_priority_missing_analysis_symbols")
    if not all(isinstance(value, list) for value in (priority, targeted, missing_analysis)):
        failures.append("explicit day Coinalyze priority coverage lists missing")
    else:
        if set(priority) != set(CORE_SYMBOLS):
            failures.append(f"day core priority mismatch: {priority}")
        if set(targeted) != set(CORE_SYMBOLS):
            failures.append(f"day core target mismatch: {targeted}")
        if missing_analysis:
            failures.append(f"day core analysis missing: {missing_analysis}")
    if worker.get("coinalyze_priority_target_coverage_complete") is not True:
        failures.append("day core target coverage is not complete")

    for symbol in CORE_SYMBOLS:
        setup = setups.get(symbol)
        flow = flows.get(symbol)
        if not isinstance(setup, dict):
            failures.append(f"{symbol}: setup missing")
            continue
        if setup.get("symbol") != symbol:
            failures.append(f"{symbol}: setup symbol mismatch")
        if not isinstance(flow, dict):
            failures.append(f"{symbol}: Flow fallback missing")
            continue
        if flow.get("symbol") != symbol:
            failures.append(f"{symbol}: Flow symbol mismatch")
        if flow.get("source_commit_sha") != expected_sha:
            failures.append(f"{symbol}: Flow source_commit_sha mismatch")
        if not flow_context_usable(flow):
            failures.append(f"{symbol}: Flow fallback has no fresh usable OI/funding field")
        if not (setup_context_usable(setup) or flow_context_usable(flow)):
            failures.append(f"{symbol}: Setup and Flow context are both unusable")
    return failures


def run_smoke(
    base_url: str,
    api_key: str,
    expected_sha: str,
    *,
    timeout: float = 15.0,
    fetch: Callable[[str, str, float], dict[str, Any]] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    version_ok = False
    for attempt in range(MAX_POLLS):
        try:
            version = _get(fetch, base_url, "/version", api_key, timeout)
            version_ok = version.get("commit_sha") == expected_sha
            if version_ok:
                break
        except (HTTPError, URLError, TimeoutError, OSError):
            pass
        except (ValueError, RuntimeError) as exc:
            print(f"FAIL phase=version error_type={type(exc).__name__}")
            return 1
        if attempt + 1 < MAX_POLLS:
            sleep(POLL_INTERVAL_SECONDS)
    if not version_ok:
        print("FAIL phase=version reason=expected_commit_not_deployed")
        return 1

    last_failures = ["new day/Flow worker snapshots not observed"]
    for attempt in range(MAX_POLLS):
        try:
            status = _get(fetch, base_url, "/v1/day-trade/status", api_key, timeout)
            setups = {
                symbol: _get(
                    fetch,
                    base_url,
                    f"/v1/day-trade/setup/{symbol}",
                    api_key,
                    timeout,
                )
                for symbol in CORE_SYMBOLS
            }
            flows = {
                symbol: _get(
                    fetch,
                    base_url,
                    f"/v1/day-trade/flow/{symbol}",
                    api_key,
                    timeout,
                )
                for symbol in CORE_SYMBOLS
            }
            last_failures = evaluate(status, setups, flows, expected_sha)
        except (HTTPError, URLError, TimeoutError, OSError):
            last_failures = ["production request failed"]
        except (ValueError, RuntimeError) as exc:
            print(f"FAIL phase=context error_type={type(exc).__name__}")
            return 1

        if not last_failures:
            worker = status["worker"]
            safe = {
                "source_commit_sha": worker.get("source_commit_sha"),
                "priority_symbols": worker.get("coinalyze_priority_symbols"),
                "priority_targeted_symbols": worker.get(
                    "coinalyze_priority_targeted_symbols"
                ),
                "setup_context_usable": {
                    symbol: setup_context_usable(setups[symbol])
                    for symbol in CORE_SYMBOLS
                },
                "flow_fallback_usable": {
                    symbol: flow_context_usable(flows[symbol])
                    for symbol in CORE_SYMBOLS
                },
            }
            print("DAY_DERIVATIVES_CONTEXT=" + json.dumps(safe, sort_keys=True))
            print("DAY DERIVATIVES PRIORITY AND FALLBACK VERIFIED.")
            return 0
        if attempt + 1 < MAX_POLLS:
            sleep(POLL_INTERVAL_SECONDS)

    print("FAIL phase=context reasons=" + " | ".join(last_failures))
    return 1


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required production smoke configuration is missing")
        return 1
    return run_smoke(base_url, api_key, expected_sha)


if __name__ == "__main__":
    sys.exit(main())
