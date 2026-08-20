#!/usr/bin/env python3
"""Fail-closed production semantic smoke for swing Coinalyze candidate coverage."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MAX_POLLS = 80
POLL_INTERVAL_SECONDS = 15
ALLOWED_STATUSES = {"GOOD", "PARTIAL", "UNAVAILABLE"}
CANDIDATE_SECTIONS = (
    "strict_longs",
    "strict_shorts",
    "watch_only_longs",
    "watch_only_shorts",
)
NO_MARKET_REASON = "no matching coinalyze future market"


def fetch_json(url: str, api_key: str, timeout: float) -> dict[str, Any]:
    request = Request(url, method="GET", headers={
        "Accept": "application/json",
        "User-Agent": "bybit-eu-swing-coinalyze-smoke/1",
        "X-Radar-Key": api_key,
    })
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _get(fetch: Callable[[str, str, float], dict[str, Any]], base_url: str,
         path: str, api_key: str, timeout: float) -> dict[str, Any]:
    return fetch(f"{base_url.rstrip('/')}{path}", api_key, timeout)


def candidate_symbols(top: dict[str, Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for section in CANDIDATE_SECTIONS:
        rows = top.get(section, [])
        if not isinstance(rows, list):
            raise ValueError(f"{section} is not a list")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"{section} contains non-object candidate")
            symbol = row.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                raise ValueError(f"{section} candidate has invalid symbol")
            if symbol not in seen:
                result.append(symbol)
                seen.add(symbol)
    return result


def candidate_rows(top: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for section in CANDIDATE_SECTIONS:
        for row in top.get(section, []):
            if isinstance(row, dict) and isinstance(row.get("symbol"), str):
                rows[row["symbol"]] = row
    return rows


def market_support_partition(top: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Classify compact candidates using the explicit no-market reason.

    UNAVAILABLE for any other reason is still treated as a supported target with
    degraded/missing endpoint data. This keeps provider-market support separate
    from endpoint payload completeness without changing strategy semantics.
    """
    supported: list[str] = []
    unsupported: list[str] = []
    for symbol, row in candidate_rows(top).items():
        status = row.get("derivatives_status")
        reason = str(row.get("derivatives_status_reason") or "").strip().lower()
        if status == "UNAVAILABLE" and NO_MARKET_REASON in reason:
            unsupported.append(symbol)
        else:
            supported.append(symbol)
    return supported, unsupported


def evaluate(top: dict[str, Any], status: dict[str, Any], expected_sha: str) -> list[str]:
    failures: list[str] = []
    worker = status.get("worker")
    if not isinstance(worker, dict):
        return ["data-status.worker missing"]
    if worker.get("source_commit_sha") != expected_sha:
        failures.append("swing worker source_commit_sha mismatch")

    priority = worker.get("coinalyze_priority_symbols")
    targeted = worker.get("coinalyze_priority_targeted_symbols")
    enriched = worker.get("coinalyze_priority_enriched_symbols")
    complete = worker.get("coinalyze_priority_complete_symbols")
    partial = worker.get("coinalyze_priority_partial_symbols")
    missing = worker.get("coinalyze_priority_missing_symbols")
    if not all(
        isinstance(value, list)
        for value in (priority, targeted, enriched, complete, partial, missing)
    ):
        failures.append("explicit Coinalyze priority coverage lists missing")
        return failures

    compact_symbols = candidate_symbols(top)
    if set(priority) != set(compact_symbols):
        failures.append(
            f"compact priority mismatch: compact={compact_symbols} priority={priority}"
        )
    if set(targeted) != set(priority):
        failures.append(
            f"not all compact priority candidates targeted: targeted={targeted} priority={priority}"
        )
    if worker.get("coinalyze_priority_full_target_coverage") is not True:
        failures.append("coinalyze_priority_full_target_coverage is not true")
    if set(enriched) | set(missing) != set(priority):
        failures.append("priority enriched/missing partition does not cover priority set")
    if set(enriched) & set(missing):
        failures.append("priority symbol appears in both enriched and missing lists")
    if set(complete) | set(partial) != set(enriched):
        failures.append("priority complete/partial partition does not equal enriched set")
    if set(complete) & set(partial):
        failures.append("priority symbol appears in both complete and partial lists")

    supported, unsupported = market_support_partition(top)
    if set(supported) | set(unsupported) != set(priority):
        failures.append("supported/unsupported market partition does not cover priority set")
    if set(supported) & set(unsupported):
        failures.append("priority symbol appears in both supported and unsupported market sets")
    if not set(supported).issubset(set(targeted)):
        failures.append(
            f"Coinalyze-supported priority candidate was not targeted: supported={supported} targeted={targeted}"
        )

    rows = candidate_rows(top)
    candidate_statuses = {
        symbol: str(row.get("derivatives_status"))
        for symbol, row in rows.items()
    }
    if {symbol for symbol, value in candidate_statuses.items() if value == "GOOD"} != set(complete):
        failures.append("GOOD candidate set does not equal complete Coinalyze priority set")
    if {symbol for symbol, value in candidate_statuses.items() if value == "PARTIAL"} != set(partial):
        failures.append("PARTIAL candidate set does not equal partial Coinalyze priority set")
    if {symbol for symbol, value in candidate_statuses.items() if value == "UNAVAILABLE"} != set(missing):
        failures.append("UNAVAILABLE candidate set does not equal missing Coinalyze priority set")
    for symbol in unsupported:
        if candidate_statuses.get(symbol) != "UNAVAILABLE":
            failures.append(f"{symbol}: unsupported Coinalyze market is not UNAVAILABLE")

    sources = status.get("sources")
    source = next(
        (row for row in sources if isinstance(row, dict) and row.get("source") == "Coinalyze"),
        None,
    ) if isinstance(sources, list) else None
    if not isinstance(source, dict):
        failures.append("Coinalyze source health row missing")
    elif source.get("status") == "ok" and (partial or missing):
        failures.append("Coinalyze source health is ok while compact candidates are incomplete")

    for symbol, row in rows.items():
        derivatives_status = row.get("derivatives_status")
        reason = row.get("derivatives_status_reason")
        if derivatives_status not in ALLOWED_STATUSES:
            failures.append(f"{symbol}: invalid derivatives_status={derivatives_status!r}")
        if not isinstance(reason, str) or not reason.strip():
            failures.append(f"{symbol}: missing derivatives_status_reason")
        if row.get("derivatives_context_only") is not True:
            failures.append(f"{symbol}: derivatives_context_only is not true")

    return failures


def run_smoke(base_url: str, api_key: str, expected_sha: str, *, timeout: float = 15.0,
              fetch: Callable[[str, str, float], dict[str, Any]] = fetch_json,
              sleep: Callable[[float], None] = time.sleep) -> int:
    version_ok = False
    last_reason = "not_checked"
    for attempt in range(MAX_POLLS):
        try:
            version = _get(fetch, base_url, "/version", api_key, timeout)
            version_ok = version.get("commit_sha") == expected_sha
            if version_ok:
                break
            last_reason = "expected_api_sha_not_deployed"
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_reason = f"version_{type(exc).__name__}"
        except (ValueError, RuntimeError) as exc:
            print(f"FAIL phase=version error_type={type(exc).__name__}")
            return 1
        if attempt + 1 < MAX_POLLS:
            sleep(POLL_INTERVAL_SECONDS)
    if not version_ok:
        print(f"FAIL phase=version reason={last_reason}")
        return 1

    last_failures: list[str] = ["worker_not_observed"]
    for attempt in range(MAX_POLLS):
        try:
            status = _get(fetch, base_url, "/v1/data-status", api_key, timeout)
            top = _get(fetch, base_url, "/v1/top-candidates?limit=3&include_watchlist=true", api_key, timeout)
            last_failures = evaluate(top, status, expected_sha)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_failures = [f"request_{type(exc).__name__}"]
        except (ValueError, RuntimeError) as exc:
            print(f"FAIL phase=semantic error_type={type(exc).__name__}")
            return 1
        if not last_failures:
            worker = status["worker"]
            supported, unsupported = market_support_partition(top)
            safe = {
                "source_commit_sha": worker.get("source_commit_sha"),
                "priority_symbols": worker.get("coinalyze_priority_symbols"),
                "priority_targeted_symbols": worker.get("coinalyze_priority_targeted_symbols"),
                "priority_enriched_symbols": worker.get("coinalyze_priority_enriched_symbols"),
                "priority_complete_symbols": worker.get("coinalyze_priority_complete_symbols"),
                "priority_partial_symbols": worker.get("coinalyze_priority_partial_symbols"),
                "priority_missing_symbols": worker.get("coinalyze_priority_missing_symbols"),
                "priority_supported_symbols": supported,
                "priority_unsupported_symbols": unsupported,
                "supported_priority_targeted": set(supported).issubset(
                    set(worker.get("coinalyze_priority_targeted_symbols") or [])
                ),
                "compact_symbols": candidate_symbols(top),
                "candidate_statuses": {
                    row["symbol"]: {
                        "status": row.get("derivatives_status"),
                        "reason": row.get("derivatives_status_reason"),
                    }
                    for section in CANDIDATE_SECTIONS
                    for row in top.get(section, [])
                },
            }
            print("SWING_COINALYZE_SEMANTICS=" + json.dumps(safe, sort_keys=True))
            print("SWING CANDIDATE COINALYZE SEMANTICS VERIFIED.")
            return 0
        if attempt + 1 < MAX_POLLS:
            sleep(POLL_INTERVAL_SECONDS)

    print("FAIL phase=semantic reasons=" + " | ".join(last_failures))
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
