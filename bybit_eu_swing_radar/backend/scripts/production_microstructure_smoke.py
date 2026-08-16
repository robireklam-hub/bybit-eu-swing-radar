#!/usr/bin/env python3
"""Read-only production smoke for the research microstructure forward recorder."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MAX_POLLS = 24
POLL_INTERVAL_SECONDS = 5
TRANSIENT_STATUSES = frozenset((404, 502, 503, 504))
AUTH_OR_RATE_LIMIT_STATUSES = frozenset((401, 403, 429))


def fetch_json(url: str, api_key: str, timeout: float) -> dict[str, Any]:
    request = Request(url, method="GET", headers={
        "Accept": "application/json",
        "User-Agent": "bybit-eu-microstructure-smoke/1",
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


def healthy(payload: dict[str, Any]) -> tuple[bool, str]:
    if payload.get("research_only") is not True:
        return False, "research_only_not_true"
    if payload.get("live_strategy_mutated") is not False:
        return False, "live_strategy_mutated_not_false"
    if payload.get("enabled") is not True:
        return False, "recorder_not_enabled"
    if payload.get("running") is not True:
        return False, "recorder_not_running"
    if payload.get("singleton_acquired") is not True:
        return False, "singleton_not_acquired"
    if payload.get("connected") is not True:
        return False, "websocket_not_connected"
    symbols = payload.get("symbols")
    if not isinstance(symbols, list) or not symbols or any(
        not isinstance(symbol, str) or not symbol.endswith("USDC") for symbol in symbols
    ):
        return False, "invalid_usdc_symbols"
    if int(payload.get("messages") or 0) <= 0:
        return False, "no_websocket_messages"
    if int(payload.get("rows_written") or 0) <= 0:
        return False, "no_database_rows_written"
    if not payload.get("last_message_at"):
        return False, "missing_last_message_at"
    if not payload.get("last_write_at"):
        return False, "missing_last_write_at"
    return True, "ok"


def readiness_healthy(payload: dict[str, Any], expected_symbols: list[str]) -> tuple[bool, str]:
    """Validate the readiness report contract without requiring the 24h gate to pass."""
    if payload.get("research_only") is not True:
        return False, "readiness_research_only_not_true"
    if payload.get("live_strategy_mutated") is not False:
        return False, "readiness_live_strategy_mutated_not_false"
    if payload.get("gate_version") != "microstructure-readiness-v1":
        return False, "unexpected_readiness_gate_version"
    if payload.get("promotion_allowed") is not False:
        return False, "readiness_promotion_allowed_not_false"
    if payload.get("error") or payload.get("error_type"):
        return False, "readiness_query_error"
    symbols = payload.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        return False, "readiness_symbols_missing"
    by_symbol = {
        item.get("symbol"): item for item in symbols
        if isinstance(item, dict) and isinstance(item.get("symbol"), str)
    }
    if set(by_symbol) != set(expected_symbols):
        return False, "readiness_symbol_set_mismatch"
    required = {
        "ready", "reasons", "bucket_count", "duration_hours", "continuity_ratio",
        "book_ready_ratio", "book_message_ratio", "trade_bucket_ratio",
        "freshness_seconds", "first_bucket_at", "last_bucket_at",
    }
    for symbol in expected_symbols:
        item = by_symbol[symbol]
        if not required.issubset(item):
            return False, f"readiness_metrics_missing:{symbol}"
        if int(item.get("bucket_count") or 0) <= 0:
            return False, f"readiness_no_buckets:{symbol}"
    return True, "ok"


def run_smoke(base_url: str, api_key: str, expected_sha: str, *, timeout: float = 15.0,
              fetch: Callable[[str, str, float], dict[str, Any]] = fetch_json,
              sleep: Callable[[float], None] = time.sleep) -> int:
    deployed = False
    last_reason = "not_checked"
    for attempt in range(MAX_POLLS):
        try:
            version = _get(fetch, base_url, "/version", api_key, timeout)
        except HTTPError as exc:
            if exc.code in AUTH_OR_RATE_LIMIT_STATUSES:
                print(f"FAIL phase=version http_status={exc.code}")
                return 1
            if exc.code not in TRANSIENT_STATUSES:
                print(f"FAIL phase=version http_status={exc.code}")
                return 1
        except (URLError, TimeoutError, OSError):
            pass
        except (ValueError, RuntimeError) as exc:
            print(f"FAIL phase=version error_type={type(exc).__name__}")
            return 1
        else:
            if version.get("commit_sha") == expected_sha:
                deployed = True
                break
        if attempt + 1 < MAX_POLLS:
            sleep(POLL_INTERVAL_SECONDS)
    if not deployed:
        print("FAIL phase=version reason=expected_commit_not_deployed")
        return 1

    recorder_status: dict[str, Any] | None = None
    for attempt in range(MAX_POLLS):
        try:
            status = _get(fetch, base_url, "/v1/research/microstructure/status", api_key, timeout)
        except HTTPError as exc:
            if exc.code in AUTH_OR_RATE_LIMIT_STATUSES:
                print(f"FAIL phase=recorder http_status={exc.code}")
                return 1
            if exc.code not in TRANSIENT_STATUSES:
                print(f"FAIL phase=recorder http_status={exc.code}")
                return 1
            last_reason = f"http_{exc.code}"
        except (URLError, TimeoutError, OSError) as exc:
            last_reason = type(exc).__name__
        except (ValueError, RuntimeError) as exc:
            print(f"FAIL phase=recorder error_type={type(exc).__name__}")
            return 1
        else:
            ok, last_reason = healthy(status)
            safe = {
                "enabled": status.get("enabled"),
                "running": status.get("running"),
                "singleton_acquired": status.get("singleton_acquired"),
                "connected": status.get("connected"),
                "symbols": status.get("symbols"),
                "messages": status.get("messages"),
                "rows_written": status.get("rows_written"),
                "last_message_at": status.get("last_message_at"),
                "last_write_at": status.get("last_write_at"),
                "last_error_at": status.get("last_error_at"),
                "last_error": status.get("last_error"),
            }
            print("RECORDER_STATUS=" + json.dumps(safe, sort_keys=True))
            if ok:
                recorder_status = status
                break
        if attempt + 1 < MAX_POLLS:
            sleep(POLL_INTERVAL_SECONDS)

    if recorder_status is None:
        print(f"FAIL phase=recorder reason={last_reason}")
        return 1

    try:
        readiness = _get(
            fetch, base_url, "/v1/research/microstructure/readiness", api_key, timeout
        )
    except HTTPError as exc:
        print(f"FAIL phase=readiness http_status={exc.code}")
        return 1
    except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL phase=readiness error_type={type(exc).__name__}")
        return 1

    expected_symbols = list(recorder_status.get("symbols") or [])
    readiness_ok, readiness_reason = readiness_healthy(readiness, expected_symbols)
    safe_readiness = {
        "gate_version": readiness.get("gate_version"),
        "ready_for_forward_feature_analysis": readiness.get("ready_for_forward_feature_analysis"),
        "promotion_allowed": readiness.get("promotion_allowed"),
        "thresholds": readiness.get("thresholds"),
        "bucket_seconds": readiness.get("bucket_seconds"),
        "symbols": readiness.get("symbols"),
        "checked_at": readiness.get("checked_at"),
    }
    print("READINESS_STATUS=" + json.dumps(safe_readiness, sort_keys=True))
    if not readiness_ok:
        print(f"FAIL phase=readiness reason={readiness_reason}")
        return 1

    print("MICROSTRUCTURE FORWARD RECORDER AND READINESS VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required microstructure smoke configuration is missing")
        return 1
    return run_smoke(base_url, api_key, expected_sha)


if __name__ == "__main__":
    sys.exit(main())
