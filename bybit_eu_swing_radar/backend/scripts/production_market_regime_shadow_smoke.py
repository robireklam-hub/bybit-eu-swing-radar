#!/usr/bin/env python3
"""Exact-SHA production smoke for research-only market-regime shadow v1."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EXPECTED_SPEC = "market-regime-shadow-v1"
MAX_POLLS = 24
POLL_INTERVAL_SECONDS = 5


def fetch_json(url: str, api_key: str, timeout: float, method: str = "GET") -> dict[str, Any]:
    request = Request(
        url,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "bybit-eu-market-regime-shadow-smoke/1",
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


def validate_capture(payload: dict[str, Any]) -> tuple[bool, str]:
    if payload.get("research_only") is not True:
        return False, "research_only_not_true"
    if payload.get("live_strategy_mutated") is not False:
        return False, "live_strategy_mutated_not_false"
    if payload.get("label_free") is not True:
        return False, "label_free_not_true"
    if payload.get("promotion_allowed") is not False:
        return False, "promotion_allowed_not_false"
    spec = payload.get("spec") or {}
    if spec.get("version") != EXPECTED_SPEC:
        return False, "unexpected_spec_version"
    if payload.get("global_regime") not in {"TREND", "RANGE", "COMPRESSION", "EXPANSION", "HIGH_VOL_STRESS", "REVERSAL"}:
        return False, "invalid_global_regime"
    if payload.get("dominant_direction") not in {"BULL", "BEAR", "NEUTRAL"}:
        return False, "invalid_dominant_direction"
    symbols = payload.get("symbols")
    if not isinstance(symbols, list) or len(symbols) < 5:
        return False, "insufficient_symbol_coverage"
    names = {item.get("symbol") for item in symbols if isinstance(item, dict)}
    if "BTCUSDC" not in names or any(not str(name).endswith("USDC") for name in names):
        return False, "invalid_usdc_universe"
    if payload.get("persisted") is not True:
        return False, "snapshot_not_persisted"
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
        capture = _call(fetch, base_url, "/v1/research/market-regime/capture", api_key, timeout, "POST")
    except HTTPError as exc:
        print(f"FAIL phase=capture http_status={exc.code}")
        return 1
    except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL phase=capture error_type={type(exc).__name__}")
        return 1

    ok, reason = validate_capture(capture)
    if not ok:
        print(f"FAIL phase=capture reason={reason}")
        return 1

    try:
        status = _call(fetch, base_url, "/v1/research/market-regime/status", api_key, timeout)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL phase=status error_type={type(exc).__name__}")
        return 1
    latest = status.get("latest") or {}
    if status.get("research_only") is not True or status.get("promotion_allowed") is not False:
        print("FAIL phase=status reason=research_contract_invalid")
        return 1
    if int(status.get("snapshot_count") or 0) < 1 or latest.get("global_regime") != capture.get("global_regime"):
        print("FAIL phase=status reason=persisted_snapshot_not_visible")
        return 1

    compact_symbols = {
        item["symbol"]: {
            "regime": item.get("regime"),
            "direction": item.get("direction"),
            "atr_ratio": (item.get("metrics") or {}).get("atr_ratio"),
            "bb_width_percentile": (item.get("metrics") or {}).get("bb_width_percentile"),
            "true_range_ratio": (item.get("metrics") or {}).get("true_range_ratio"),
            "turnover_ratio": (item.get("metrics") or {}).get("turnover_ratio"),
            "trend_efficiency_ratio": (item.get("metrics") or {}).get("trend_efficiency_ratio"),
        }
        for item in capture.get("symbols", [])
        if isinstance(item, dict) and item.get("symbol")
    }
    safe = {
        "captured_at": capture.get("captured_at"),
        "captured_hour": capture.get("captured_hour"),
        "source_commit_sha": capture.get("source_commit_sha"),
        "global_regime": capture.get("global_regime"),
        "dominant_direction": capture.get("dominant_direction"),
        "btc_anchor": capture.get("btc_anchor"),
        "universe_size": capture.get("universe_size"),
        "coverage_pct": capture.get("coverage_pct"),
        "regime_counts": capture.get("regime_counts"),
        "direction_counts": capture.get("direction_counts"),
        "symbols": compact_symbols,
    }
    print("MARKET_REGIME_SHADOW=" + json.dumps(safe, sort_keys=True))
    print("MARKET REGIME SHADOW V1 VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required market-regime smoke configuration is missing")
        return 1
    return run_smoke(base_url, api_key, expected_sha)


if __name__ == "__main__":
    sys.exit(main())
