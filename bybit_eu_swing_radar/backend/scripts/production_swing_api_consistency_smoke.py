#!/usr/bin/env python3
"""Fail-closed production smoke for swing API/read-path isolation."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MAX_POLLS = 80
POLL_INTERVAL_SECONDS = 15
MAX_AGENT_SCAN_BYTES = 80_000
CANDIDATE_SECTIONS = (
    "strict_longs",
    "strict_shorts",
    "watch_only_longs",
    "watch_only_shorts",
)
DERIVATIVE_VALUE_FIELDS = (
    "open_interest_usd",
    "oi_change_1h_pct",
    "oi_change_4h_pct",
    "oi_change_24h_pct",
    "funding_rate",
    "long_liquidations_24h_usd",
    "short_liquidations_24h_usd",
)


def fetch_json(
    url: str,
    api_key: str,
    timeout: float,
    *,
    user_agent: str = "bybit-eu-swing-api-consistency-smoke/1",
) -> dict[str, Any]:
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": user_agent,
            "X-Radar-Key": api_key,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _iso(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp missing")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _candidate_rows(top: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for section in CANDIDATE_SECTIONS:
        rows = top.get(section, [])
        if not isinstance(rows, list):
            raise ValueError(f"{section} is not a list")
        result.extend(row for row in rows if isinstance(row, dict))
    return result


def evaluate(
    compact: dict[str, Any],
    full: dict[str, Any],
    top: dict[str, Any],
    setups: dict[str, dict[str, Any]],
    status: dict[str, Any],
    expected_sha: str,
) -> list[str]:
    failures: list[str] = []
    worker = status.get("worker")
    if not isinstance(worker, dict):
        return ["data-status.worker missing"]
    if worker.get("source_commit_sha") != expected_sha:
        failures.append("swing worker source_commit_sha mismatch")

    for field in ("extended_watchlist", "liquidity_blocked", "momentum_radar", "exclusions"):
        if compact.get(field) not in ([], None):
            failures.append(f"agent scan did not compact {field}")

    compact_bytes = len(json.dumps(compact, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    if compact_bytes > MAX_AGENT_SCAN_BYTES:
        failures.append(f"agent scan remains oversized: {compact_bytes}>{MAX_AGENT_SCAN_BYTES}")

    if _iso(compact.get("data_as_of")) != _iso(full.get("data_as_of")):
        failures.append("compact/full scan snapshot timestamp mismatch")
    for side in ("longs", "shorts"):
        compact_symbols = [row.get("symbol") for row in compact.get(side, [])]
        full_symbols = [row.get("symbol") for row in full.get(side, [])]
        if compact_symbols != full_symbols:
            failures.append(f"compact/full {side} ranking mismatch")

    expected_watch = int(worker.get("extended_watchlist_items") or 0)
    expected_blocked = int(worker.get("liquidity_blocked_items") or 0)
    if expected_watch and len(full.get("extended_watchlist") or []) != expected_watch:
        failures.append("research full scan lost extended_watchlist coverage")
    if expected_blocked and len(full.get("liquidity_blocked") or []) != expected_blocked:
        failures.append("research full scan lost liquidity_blocked coverage")

    top_as_of = _iso(top.get("data_as_of"))
    for row in _candidate_rows(top):
        symbol = row.get("symbol")
        setup = setups.get(str(symbol))
        if not setup:
            failures.append(f"{symbol}: fresh symbol setup missing")
            continue
        if _iso(setup.get("data_as_of")) != top_as_of:
            failures.append(f"{symbol}: symbol setup is not from current latest_scan snapshot")

        status_name = row.get("derivatives_status")
        reason = row.get("derivatives_status_reason")
        if status_name not in {"GOOD", "PARTIAL", "UNAVAILABLE"}:
            failures.append(f"{symbol}: invalid derivatives_status={status_name!r}")
        if not isinstance(reason, str) or not reason.strip():
            failures.append(f"{symbol}: derivatives_status_reason missing")
        if status_name == "PARTIAL":
            derivatives = row.get("derivatives") or {}
            for field in DERIVATIVE_VALUE_FIELDS:
                if derivatives.get(field) is None and field not in reason:
                    failures.append(f"{symbol}: PARTIAL reason omits {field}")

    return failures


def run_smoke(
    base_url: str,
    api_key: str,
    expected_sha: str,
    *,
    timeout: float = 20.0,
    fetch: Callable[..., dict[str, Any]] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    for attempt in range(MAX_POLLS):
        try:
            version = fetch(f"{base_url.rstrip('/')}/version", api_key, timeout)
            status = fetch(f"{base_url.rstrip('/')}/v1/data-status", api_key, timeout)
            worker = status.get("worker") or {}
            if version.get("commit_sha") == expected_sha and worker.get("source_commit_sha") == expected_sha:
                break
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            pass
        if attempt + 1 >= MAX_POLLS:
            print("FAIL phase=deployment reason=exact_api_and_worker_sha_not_observed")
            return 1
        sleep(POLL_INTERVAL_SECONDS)

    try:
        path = "/v1/scan?direction=both&limit=10&min_score=0"
        compact = fetch(f"{base_url.rstrip('/')}{path}", api_key, timeout)
        full = fetch(
            f"{base_url.rstrip('/')}{path}",
            api_key,
            timeout,
            user_agent="swing-liquidity-shadow/smoke",
        )
        top = fetch(
            f"{base_url.rstrip('/')}/v1/top-candidates?limit=3&include_watchlist=true",
            api_key,
            timeout,
        )
        setups: dict[str, dict[str, Any]] = {}
        for row in _candidate_rows(top):
            symbol = str(row.get("symbol") or "")
            if symbol and symbol not in setups:
                setups[symbol] = fetch(
                    f"{base_url.rstrip('/')}/v1/setup/{symbol}", api_key, timeout
                )
        failures = evaluate(compact, full, top, setups, status, expected_sha)

        full_candidates = [
            row
            for section in ("longs", "shorts", "extended_watchlist", "liquidity_blocked")
            for row in (full.get(section) or [])
            if isinstance(row, dict) and row.get("symbol")
        ]
        if full_candidates:
            symbol = str(full_candidates[0]["symbol"])
            book = fetch(
                f"{base_url.rstrip('/')}/v1/research/swing-liquidity/orderbook/{symbol}",
                api_key,
                timeout,
            )
            if book.get("research_only") is not True or book.get("live_strategy_mutated") is not False:
                failures.append("research orderbook proxy invariant failed")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"FAIL phase=semantic error_type={type(exc).__name__}")
        return 1

    if failures:
        print("FAIL phase=semantic reasons=" + " | ".join(failures))
        return 1

    safe = {
        "source_commit_sha": expected_sha,
        "agent_scan_bytes": len(json.dumps(compact, separators=(",", ":")).encode("utf-8")),
        "agent_long_count": len(compact.get("longs") or []),
        "agent_short_count": len(compact.get("shorts") or []),
        "research_watch_count": len(full.get("extended_watchlist") or []),
        "research_liquidity_blocked_count": len(full.get("liquidity_blocked") or []),
        "top_candidate_count": len(_candidate_rows(top)),
        "fresh_symbol_setup_count": len(setups),
    }
    print("SWING_API_CONSISTENCY=" + json.dumps(safe, sort_keys=True))
    print("SWING API/RESEARCH ISOLATION VERIFIED.")
    return 0


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
