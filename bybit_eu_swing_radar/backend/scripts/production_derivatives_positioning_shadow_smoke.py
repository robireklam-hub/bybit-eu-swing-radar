#!/usr/bin/env python3
"""Exact-SHA production smoke for research-only derivatives positioning v1."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EXPECTED_SPEC = "derivatives-positioning-shadow-v1"
MAX_POLLS = 24
POLL_INTERVAL_SECONDS = 5
VALID_POSITIONING = {
    "LONG_BUILD", "SHORT_BUILD", "LONG_DELEVERAGING", "SHORT_COVERING",
    "MIXED", "INSUFFICIENT_DATA",
}


def fetch_json(url: str, api_key: str, timeout: float, method: str = "GET") -> dict[str, Any]:
    request = Request(
        url,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "bybit-eu-derivatives-positioning-smoke/1",
            "X-Radar-Key": api_key,
        },
        data=b"{}" if method == "POST" else None,
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _call(fetch: Callable[..., dict[str, Any]], base_url: str, path: str, api_key: str, timeout: float, method: str = "GET") -> dict[str, Any]:
    return fetch(f"{base_url.rstrip('/')}{path}", api_key, timeout, method)


def validate_capture(payload: dict[str, Any], expected_sha: str) -> tuple[bool, str]:
    if payload.get("research_only") is not True:
        return False, "research_only_not_true"
    if payload.get("label_free") is not True:
        return False, "label_free_not_true"
    if payload.get("live_strategy_mutated") is not False:
        return False, "live_strategy_mutated_not_false"
    if payload.get("promotion_allowed") is not False:
        return False, "promotion_allowed_not_false"
    if payload.get("spec_version") != EXPECTED_SPEC:
        return False, "unexpected_spec_version"
    if payload.get("source_commit_sha") != expected_sha:
        return False, "unexpected_source_commit_sha"
    if payload.get("persisted") is not True:
        return False, "snapshot_not_persisted"
    symbols = payload.get("symbols")
    if not isinstance(symbols, dict) or len(symbols) < 5:
        return False, "insufficient_symbol_coverage"
    if any(not str(symbol).endswith("USDC") for symbol in symbols):
        return False, "non_usdc_symbol_present"
    for row in symbols.values():
        if not isinstance(row, dict):
            return False, "invalid_symbol_payload"
        if row.get("positioning_state") not in VALID_POSITIONING:
            return False, "invalid_positioning_state"
        if row.get("derivatives_context_only") is not True or row.get("execution_proof") is not False:
            return False, "execution_contract_invalid"
    coverage = payload.get("coverage") or {}
    if int(coverage.get("market_regime") or 0) < 5:
        return False, "insufficient_regime_coverage"
    if int(coverage.get("flow") or 0) < 1:
        return False, "no_flow_coverage"
    return True, "ok"


def run_smoke(
    base_url: str,
    api_key: str,
    expected_sha: str,
    *,
    timeout: float = 20.0,
    fetch: Callable[..., dict[str, Any]] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    deployed = False
    for attempt in range(MAX_POLLS):
        try:
            version = _call(fetch, base_url, "/version", api_key, timeout)
        except (HTTPError, URLError, TimeoutError, OSError):
            version = {}
        if version.get("commit_sha") == expected_sha:
            deployed = True
            break
        if attempt + 1 < MAX_POLLS:
            sleep(POLL_INTERVAL_SECONDS)
    if not deployed:
        print("FAIL phase=version reason=expected_commit_not_deployed")
        return 1

    try:
        capture = _call(
            fetch, base_url, "/v1/research/derivatives-positioning/capture",
            api_key, timeout, "POST",
        )
    except HTTPError as exc:
        print(f"FAIL phase=capture http_status={exc.code}")
        return 1
    except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL phase=capture error_type={type(exc).__name__}")
        return 1

    ok, reason = validate_capture(capture, expected_sha)
    if not ok:
        print(f"FAIL phase=capture reason={reason}")
        return 1

    try:
        status = _call(fetch, base_url, "/v1/research/derivatives-positioning/status", api_key, timeout)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL phase=status error_type={type(exc).__name__}")
        return 1
    latest = status.get("latest") or {}
    if status.get("research_only") is not True or status.get("promotion_allowed") is not False:
        print("FAIL phase=status reason=research_contract_invalid")
        return 1
    if int(status.get("snapshot_count") or 0) < 1 or latest.get("captured_at") != capture.get("captured_at"):
        print("FAIL phase=status reason=persisted_snapshot_not_visible")
        return 1

    compact = {
        symbol: {
            "positioning_state": row.get("positioning_state"),
            "funding_crowding": row.get("funding_crowding"),
            "liquidation_state": (row.get("liquidations") or {}).get("state"),
            "market_regime": row.get("market_regime"),
            "market_direction": row.get("market_direction"),
            "regime_interaction": row.get("regime_interaction"),
            "oi_change_15m_pct": row.get("oi_change_15m_pct"),
            "funding_rate_decimal": row.get("funding_rate_decimal"),
        }
        for symbol, row in (capture.get("symbols") or {}).items()
        if isinstance(row, dict)
    }
    safe = {
        "captured_at": capture.get("captured_at"),
        "captured_hour": capture.get("captured_hour"),
        "source_commit_sha": capture.get("source_commit_sha"),
        "symbol_count": capture.get("symbol_count"),
        "coverage": capture.get("coverage"),
        "positioning_counts": capture.get("positioning_counts"),
        "crowding_counts": capture.get("crowding_counts"),
        "interaction_counts": capture.get("interaction_counts"),
        "upstream": capture.get("upstream"),
        "symbols": compact,
    }
    print("DERIVATIVES_POSITIONING_SHADOW=" + json.dumps(safe, sort_keys=True))
    print("DERIVATIVES POSITIONING SHADOW V1 VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required derivatives-positioning smoke configuration is missing")
        return 1
    return run_smoke(base_url, api_key, expected_sha)


if __name__ == "__main__":
    sys.exit(main())
