#!/usr/bin/env python3
"""Read-only semantic verifier for durable swing-liquidity forward capture state."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.request import Request, urlopen

STATUS_PATH = "/v1/research/swing-liquidity/forward-status"
EXPECTED_STUDY = "swing-liquidity-validation-v1"
MAX_CAPTURE_AGE_SECONDS = 20 * 60
BELOW_100K_TIERS = frozenset(("LT_25K", "25K_50K", "50K_100K", "<25k", "25k-50k", "50k-100k"))
AT_OR_ABOVE_100K_TIERS = frozenset(("100K_250K", "250K_1M", "GE_1M", "100-250k", "250k-1m", ">=1m"))


def parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("last_capture_at is missing")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("last_capture_at must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def fetch_json(url: str, api_key: str, timeout: float) -> dict[str, Any]:
    request = Request(url, method="GET", headers={
        "Accept": "application/json",
        "User-Agent": "bybit-eu-swing-liquidity-forward-status/1",
        "X-Radar-Key": api_key,
    })
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _has_positive_tier_count(tiers: dict[str, Any], accepted: frozenset[str]) -> bool:
    for name, raw_count in tiers.items():
        if name not in accepted:
            continue
        try:
            if int(raw_count) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def validate_status(payload: dict[str, Any], *, now: datetime | None = None) -> list[str]:
    errors: list[str] = []
    if payload.get("research_only") is not True:
        errors.append("research_only_not_true")
    if payload.get("live_strategy_mutated") is not False:
        errors.append("live_strategy_mutated_not_false")
    if payload.get("promotion_allowed") is not False:
        errors.append("promotion_allowed_not_false")
    if payload.get("study") != EXPECTED_STUDY:
        errors.append("unexpected_study")

    try:
        capture_count = int(payload.get("capture_count") or 0)
        candidate_observations = int(payload.get("candidate_observations") or 0)
    except (TypeError, ValueError):
        errors.append("invalid_counts")
        return errors
    if capture_count <= 0:
        errors.append("no_durable_captures")
    if candidate_observations <= 0:
        errors.append("no_durable_observations")

    try:
        last_capture = parse_timestamp(payload.get("last_capture_at"))
    except ValueError as exc:
        errors.append(str(exc))
    else:
        reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        age = (reference - last_capture).total_seconds()
        if age < -60:
            errors.append("last_capture_at_in_future")
        elif age > MAX_CAPTURE_AGE_SECONDS:
            errors.append(f"stale_last_capture:{age:.1f}s")

    turnover_tiers = payload.get("turnover_tiers")
    spread_tiers = payload.get("spread_tiers")
    if not isinstance(turnover_tiers, dict) or not turnover_tiers:
        errors.append("missing_turnover_tier_coverage")
    else:
        if not _has_positive_tier_count(turnover_tiers, BELOW_100K_TIERS):
            errors.append("missing_below_100k_research_exposure")
        if not _has_positive_tier_count(turnover_tiers, AT_OR_ABOVE_100K_TIERS):
            errors.append("missing_current_gate_comparator_exposure")
    if not isinstance(spread_tiers, dict) or not spread_tiers:
        errors.append("missing_spread_tier_coverage")
    return errors


def run_check(base_url: str, api_key: str, *, timeout: float = 15.0,
              fetch: Callable[[str, str, float], dict[str, Any]] = fetch_json,
              now: datetime | None = None) -> int:
    payload = fetch(f"{base_url.rstrip('/')}{STATUS_PATH}", api_key, timeout)
    safe = {
        "research_only": payload.get("research_only"),
        "live_strategy_mutated": payload.get("live_strategy_mutated"),
        "promotion_allowed": payload.get("promotion_allowed"),
        "study": payload.get("study"),
        "capture_count": payload.get("capture_count"),
        "first_capture_at": payload.get("first_capture_at"),
        "last_capture_at": payload.get("last_capture_at"),
        "candidate_observations": payload.get("candidate_observations"),
        "orderbook_errors": payload.get("orderbook_errors"),
        "turnover_tiers": payload.get("turnover_tiers"),
        "spread_tiers": payload.get("spread_tiers"),
        "development_target_matured_events": payload.get("development_target_matured_events"),
        "validation_target_matured_events": payload.get("validation_target_matured_events"),
    }
    print("SWING_LIQUIDITY_FORWARD_STATUS=" + json.dumps(safe, sort_keys=True))
    errors = validate_status(payload, now=now)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print("SWING LIQUIDITY DURABLE FORWARD CAPTURE VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    if not base_url or not api_key:
        print("FAIL required production status configuration is missing")
        return 1
    try:
        return run_check(base_url, api_key)
    except Exception as exc:
        print(f"FAIL request_error={type(exc).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
