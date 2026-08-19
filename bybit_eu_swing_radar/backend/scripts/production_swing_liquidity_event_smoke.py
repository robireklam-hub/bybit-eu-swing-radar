#!/usr/bin/env python3
"""Fail-closed production smoke for label-blind swing-liquidity event construction."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MAX_POLLS = 80
POLL_INTERVAL_SECONDS = 15
EVENT_PATH = "/v1/research/swing-liquidity/forward-event-status"
FORBIDDEN_EVENT_KEYS = {
    "outcome",
    "gross_r",
    "net_r",
    "mfe_r",
    "mae_r",
    "closed_at",
    "exit_price",
    "exit_reason",
    "future_return",
    "future_returns",
}


def fetch_json(url: str, api_key: str, timeout: float) -> dict[str, Any]:
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "bybit-eu-swing-liquidity-event-smoke/1",
            "X-Radar-Key": api_key,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _parse_ts(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} missing")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def validate_event_payload(payload: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    expected_flags = {
        "research_only": True,
        "label_blind": True,
        "outcome_visible": False,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
    }
    for key, expected in expected_flags.items():
        if payload.get(key) is not expected:
            failures.append(f"{key}_unexpected")
    if payload.get("study") != "swing-liquidity-validation-v1":
        failures.append("unexpected_study")
    if payload.get("builder_version") != "swing-liquidity-event-builder-v1":
        failures.append("unexpected_builder_version")
    if payload.get("event_identity") != "symbol_side_first_qualifying_4h_trigger_bar":
        failures.append("unexpected_event_identity")

    try:
        durable_rows = int(payload.get("durable_snapshot_rows") or 0)
        symbol_count = int(payload.get("symbol_count") or 0)
        kline_symbol_count = int(payload.get("kline_symbol_count") or 0)
        event_count = int(payload.get("event_count") or 0)
        matured_count = int(payload.get("matured_event_count") or 0)
    except (TypeError, ValueError):
        return failures + ["invalid_counts"]
    if durable_rows <= 0:
        failures.append("no_durable_snapshot_rows")
    if symbol_count <= 0:
        failures.append("no_symbols")
    if kline_symbol_count != symbol_count:
        failures.append(f"incomplete_kline_coverage:{kline_symbol_count}/{symbol_count}")
    if matured_count < 0 or event_count < 0 or matured_count > event_count:
        failures.append("invalid_event_maturity_counts")

    errors = payload.get("kline_errors")
    if not isinstance(errors, dict):
        failures.append("kline_errors_not_object")
    elif errors:
        failures.append("kline_errors_present:" + ",".join(sorted(errors)))

    events = payload.get("events")
    if not isinstance(events, list):
        return failures + ["events_not_list"]
    if len(events) != event_count:
        failures.append("event_count_mismatch")
    checked_at = None
    try:
        checked_at = _parse_ts(payload.get("checked_at"), "checked_at")
    except ValueError as exc:
        failures.append(str(exc))

    ids: set[str] = set()
    trigger_keys: set[tuple[str, str, str]] = set()
    for index, event in enumerate(events):
        prefix = f"event[{index}]"
        if not isinstance(event, dict):
            failures.append(f"{prefix}_not_object")
            continue
        if event.get("research_only") is not True or event.get("label_blind") is not True:
            failures.append(f"{prefix}_not_label_blind")
        if event.get("outcome_visible") is not False or event.get("promotion_allowed") is not False:
            failures.append(f"{prefix}_unsafe_flags")
        forbidden = sorted(FORBIDDEN_EVENT_KEYS.intersection(str(key).lower() for key in event))
        if forbidden:
            failures.append(f"{prefix}_forbidden_keys:" + ",".join(forbidden))

        symbol = str(event.get("symbol") or "").upper()
        side = str(event.get("side") or "").lower()
        trigger_raw = str(event.get("trigger_close_at") or "")
        if not symbol or side not in {"long", "short"}:
            failures.append(f"{prefix}_invalid_symbol_side")
        elif trigger_raw:
            trigger_key = (symbol, side, trigger_raw)
            if trigger_key in trigger_keys:
                failures.append(f"{prefix}_duplicate_symbol_side_trigger_bar")
            else:
                trigger_keys.add(trigger_key)

        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            failures.append(f"{prefix}_missing_event_id")
        elif event_id in ids:
            failures.append(f"{prefix}_duplicate_event_id")
        else:
            ids.add(event_id)
        try:
            captured = _parse_ts(event.get("pretrigger_captured_at"), f"{prefix}.pretrigger_captured_at")
            available_raw = event.get("pretrigger_available_at") or event.get("pretrigger_captured_at")
            available = _parse_ts(available_raw, f"{prefix}.pretrigger_available_at")
            trigger = _parse_ts(event.get("trigger_close_at"), f"{prefix}.trigger_close_at")
            matures = _parse_ts(event.get("matures_at"), f"{prefix}.matures_at")
            age = float(event.get("pretrigger_snapshot_age_seconds"))
        except (ValueError, TypeError) as exc:
            failures.append(f"{prefix}_invalid_timestamps:{type(exc).__name__}")
            continue
        if event.get("point_in_time_verified") is True and not event.get("pretrigger_available_at"):
            failures.append(f"{prefix}_verified_pit_missing_available_at")
        if available < captured:
            failures.append(f"{prefix}_availability_precedes_capture")
        if not (0 < age <= 90 * 60):
            failures.append(f"{prefix}_snapshot_age_out_of_range")
        if abs((trigger - available).total_seconds() - age) > 1.0:
            failures.append(f"{prefix}_snapshot_age_mismatch")
        if (matures - trigger).total_seconds() != 10 * 24 * 3600:
            failures.append(f"{prefix}_wrong_maturity_horizon")
        if checked_at is not None and trigger > checked_at:
            failures.append(f"{prefix}_future_trigger")
    return failures


def run_smoke(
    base_url: str,
    api_key: str,
    expected_sha: str,
    *,
    timeout: float = 90.0,
    fetch: Callable[[str, str, float], dict[str, Any]] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    version_ok = False
    last_reason = "not_checked"
    for attempt in range(MAX_POLLS):
        try:
            version = fetch(f"{base_url.rstrip('/')}/version", api_key, timeout)
            if version.get("commit_sha") == expected_sha:
                version_ok = True
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

    try:
        payload = fetch(f"{base_url.rstrip('/')}{EVENT_PATH}", api_key, timeout)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL phase=event_status error_type={type(exc).__name__}")
        return 1
    failures = validate_event_payload(payload)
    safe = {
        "study": payload.get("study"),
        "builder_version": payload.get("builder_version"),
        "event_identity": payload.get("event_identity"),
        "checked_at": payload.get("checked_at"),
        "durable_snapshot_rows": payload.get("durable_snapshot_rows"),
        "symbol_count": payload.get("symbol_count"),
        "kline_symbol_count": payload.get("kline_symbol_count"),
        "kline_errors": payload.get("kline_errors"),
        "event_count": payload.get("event_count"),
        "matured_event_count": payload.get("matured_event_count"),
        "promotion_allowed": payload.get("promotion_allowed"),
    }
    print("SWING_LIQUIDITY_EVENT_STATUS=" + json.dumps(safe, sort_keys=True))
    if failures:
        print("FAIL phase=semantic reasons=" + " | ".join(failures))
        return 1
    print("SWING LIQUIDITY LABEL-BLIND EVENT CONSTRUCTION VERIFIED.")
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
