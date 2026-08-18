#!/usr/bin/env python3
"""Exact-SHA production smoke for bounded read-only microstructure bucket access."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SYMBOLS = ("BTCUSDC", "ETHUSDC", "SOLUSDC")
LOOKBACK_MINUTES = 15
LIMIT = 240
MAX_FRESHNESS_SECONDS = 180.0


def fetch_json(url: str, api_key: str, timeout: float = 20.0) -> dict[str, Any]:
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "bybit-eu-microstructure-data-access-smoke/1",
            "X-Radar-Key": api_key,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _parse_dt(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def validate_bucket_payload(payload: dict[str, Any], symbol: str) -> tuple[bool, str]:
    if payload.get("research_only") is not True:
        return False, "research_only_not_true"
    if payload.get("live_strategy_mutated") is not False:
        return False, "live_strategy_mutated_not_false"
    if payload.get("label_blind") is not True:
        return False, "label_blind_not_true"
    if payload.get("outcome_fields_read") is not False:
        return False, "outcome_fields_read_not_false"
    if payload.get("promotion_allowed") is not False:
        return False, "promotion_allowed_not_false"
    if payload.get("source_table") != "microstructure_buckets":
        return False, "unexpected_source_table"
    if payload.get("symbol") != symbol:
        return False, "symbol_mismatch"
    if payload.get("lookback_minutes") != LOOKBACK_MINUTES or payload.get("limit") != LIMIT:
        return False, "query_bounds_mismatch"
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        return False, "rows_missing"
    if int(payload.get("row_count") or 0) != len(rows):
        return False, "row_count_mismatch"
    if len(rows) > LIMIT:
        return False, "row_limit_exceeded"
    summary = payload.get("summary")
    if not isinstance(summary, dict) or int(summary.get("row_count") or 0) != len(rows):
        return False, "summary_missing_or_inconsistent"
    required_summary = {
        "book_ready_ratio",
        "trade_bucket_ratio",
        "trade_count",
        "book_message_count",
        "total_quote_volume",
        "signed_quote_flow",
        "mean_spread_bps",
        "p95_spread_bps",
        "mean_imbalance_10",
        "mean_imbalance_50",
        "mean_microprice_displacement_bps",
        "mean_book_pressure_ratio",
    }
    if not required_summary.issubset(summary):
        return False, "summary_contract_incomplete"
    required_row = {
        "symbol",
        "bucket_start",
        "bucket_seconds",
        "spread_bps",
        "microprice",
        "imbalance_10",
        "imbalance_50",
        "signed_quote_flow",
        "total_quote_volume",
        "bid_added_quote",
        "bid_removed_quote",
        "ask_added_quote",
        "ask_removed_quote",
        "book_ready",
    }
    for row in rows:
        if not isinstance(row, dict) or not required_row.issubset(row):
            return False, "row_contract_incomplete"
        if row.get("symbol") != symbol:
            return False, "row_symbol_mismatch"
        if "net_r" in row or "outcome" in row or "exit_reason" in row:
            return False, "outcome_field_leakage"
    try:
        last_bucket = _parse_dt(payload.get("last_bucket_at"))
    except ValueError:
        return False, "last_bucket_at_invalid"
    freshness = (datetime.now(timezone.utc) - last_bucket).total_seconds()
    if freshness < -10 or freshness > MAX_FRESHNESS_SECONDS:
        return False, "bucket_data_stale"
    return True, "ok"


def _safe_sample(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload["rows"]
    latest = rows[-1]
    return {
        "row_count": payload.get("row_count"),
        "first_bucket_at": payload.get("first_bucket_at"),
        "last_bucket_at": payload.get("last_bucket_at"),
        "summary": payload.get("summary"),
        "latest": {
            "bucket_start": latest.get("bucket_start"),
            "spread_bps": latest.get("spread_bps"),
            "microprice": latest.get("microprice"),
            "imbalance_10": latest.get("imbalance_10"),
            "imbalance_50": latest.get("imbalance_50"),
            "signed_quote_flow": latest.get("signed_quote_flow"),
            "total_quote_volume": latest.get("total_quote_volume"),
            "book_ready": latest.get("book_ready"),
        },
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

    samples: dict[str, Any] = {}
    for symbol in SYMBOLS:
        query = urlencode(
            {
                "symbol": symbol,
                "lookback_minutes": LOOKBACK_MINUTES,
                "limit": LIMIT,
            }
        )
        try:
            payload = fetch_json(
                f"{base}/v1/research/microstructure/buckets?{query}",
                api_key,
            )
        except HTTPError as exc:
            print(f"FAIL phase=buckets symbol={symbol} http_status={exc.code}")
            return 1
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"FAIL phase=buckets symbol={symbol} error_type={type(exc).__name__}")
            return 1
        ok, reason = validate_bucket_payload(payload, symbol)
        if not ok:
            print(f"FAIL phase=buckets symbol={symbol} reason={reason}")
            return 1
        samples[symbol] = _safe_sample(payload)

    print(
        "MICROSTRUCTURE_DATA_SAMPLE="
        + json.dumps(
            {
                "lookback_minutes": LOOKBACK_MINUTES,
                "limit": LIMIT,
                "symbols": samples,
            },
            sort_keys=True,
        )
    )
    print("MICROSTRUCTURE READ-ONLY DATA ACCESS VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key or not expected_sha:
        print("FAIL required data access smoke configuration is missing")
        return 1
    return run(base_url, api_key, expected_sha)


if __name__ == "__main__":
    sys.exit(main())
