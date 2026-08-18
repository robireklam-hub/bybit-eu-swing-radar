#!/usr/bin/env python3
"""Exact-SHA production smoke for BTC Macro / Cycle / ETF Intelligence v1."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EXPECTED_SPEC = "btc-macro-cycle-etf-shadow-v1"
MAX_POLLS = 30
POLL_INTERVAL_SECONDS = 5


def fetch_json(url: str, api_key: str, timeout: float, method: str = "GET") -> dict[str, Any]:
    request = Request(
        url,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json", "X-Radar-Key": api_key},
        data=b"{}" if method == "POST" else None,
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _call(fetch: Callable[..., dict[str, Any]], base: str, path: str, key: str, timeout: float, method: str = "GET") -> dict[str, Any]:
    return fetch(f"{base.rstrip('/')}{path}", key, timeout, method)


def validate_capture(payload: dict[str, Any]) -> tuple[bool, str]:
    if payload.get("research_only") is not True or payload.get("label_free") is not True or payload.get("context_only") is not True:
        return False, "research_contract_invalid"
    if payload.get("live_strategy_mutated") is not False or payload.get("promotion_allowed") is not False:
        return False, "mutation_or_promotion_contract_invalid"
    if (payload.get("spec") or {}).get("version") != EXPECTED_SPEC:
        return False, "unexpected_spec"
    if payload.get("persisted") is not True:
        return False, "not_persisted"
    cycle = payload.get("cycle") or {}
    if int(cycle.get("tip_height") or 0) < 840000:
        return False, "invalid_cycle_tip"
    price = payload.get("btc_price") or {}
    if price.get("symbol") != "BTCUSDC" or int(price.get("data_points") or 0) < 200:
        return False, "btc_price_coverage_invalid"
    statuses = ((payload.get("coverage") or {}).get("source_status") or {})
    live_macro = sum(1 for key, value in statuses.items() if key.startswith("fred_") and (value or {}).get("status") == "LIVE")
    if live_macro < 2:
        return False, "insufficient_live_macro_series"
    if (statuses.get("etf_flows") or {}).get("status") != "LIVE" or payload.get("etf") is None:
        return False, "etf_flow_source_not_live"
    return True, "ok"


def run_smoke(base_url: str, api_key: str, expected_sha: str, *, timeout: float = 40.0, fetch: Callable[..., dict[str, Any]] = fetch_json, sleep: Callable[[float], None] = time.sleep) -> int:
    for attempt in range(MAX_POLLS):
        try:
            version = _call(fetch, base_url, "/version", api_key, timeout)
        except (HTTPError, URLError, TimeoutError, OSError):
            version = {}
        if version.get("commit_sha") == expected_sha:
            break
        if attempt + 1 == MAX_POLLS:
            print("FAIL phase=version reason=expected_commit_not_deployed")
            return 1
        sleep(POLL_INTERVAL_SECONDS)
    try:
        capture = _call(fetch, base_url, "/v1/research/btc-macro-cycle-etf/capture", api_key, timeout, "POST")
    except HTTPError as exc:
        print(f"FAIL phase=capture http_status={exc.code}")
        return 1
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"FAIL phase=capture error_type={type(exc).__name__}")
        return 1
    ok, reason = validate_capture(capture)
    if not ok:
        print(f"FAIL phase=capture reason={reason}")
        print("CAPTURE_SAFE=" + json.dumps({"coverage": capture.get("coverage"), "cycle": capture.get("cycle"), "btc_price": capture.get("btc_price")}, sort_keys=True))
        return 1
    status = _call(fetch, base_url, "/v1/research/btc-macro-cycle-etf/status", api_key, timeout)
    latest = status.get("latest") or {}
    if int(status.get("snapshot_count") or 0) < 1:
        print("FAIL phase=status reason=no_snapshot")
        return 1
    if latest.get("captured_hour") != capture.get("captured_hour") or latest.get("source_commit_sha") != expected_sha:
        print("FAIL phase=status reason=persisted_snapshot_not_visible")
        return 1
    safe = {
        "captured_at": capture.get("captured_at"),
        "captured_hour": capture.get("captured_hour"),
        "source_commit_sha": capture.get("source_commit_sha"),
        "cycle": capture.get("cycle"),
        "btc_price": capture.get("btc_price"),
        "macro": capture.get("macro"),
        "etf": capture.get("etf"),
        "source_status": ((capture.get("coverage") or {}).get("source_status") or {}),
    }
    print("BTC_MACRO_CYCLE_ETF_SHADOW=" + json.dumps(safe, sort_keys=True))
    print("BTC MACRO CYCLE ETF SHADOW V1 VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required BTC macro/cycle/ETF smoke configuration is missing")
        return 1
    return run_smoke(base_url, api_key, expected_sha)


if __name__ == "__main__":
    sys.exit(main())
