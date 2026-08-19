from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.request import Request, urlopen


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}_missing")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field}_invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field}_timezone_missing")
    return parsed.astimezone(timezone.utc)


def summarize_maturity_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Derive label-blind maturity readiness without reading any trade outcome."""
    if payload.get("research_only") is not True:
        raise ValueError("research_only_not_true")
    if payload.get("label_blind") is not True:
        raise ValueError("label_blind_not_true")
    if payload.get("outcome_visible") is not False:
        raise ValueError("outcome_visible_not_false")
    if payload.get("promotion_allowed") is not False:
        raise ValueError("promotion_allowed_not_false")

    checked_at = _parse_timestamp(payload.get("checked_at"), "checked_at")
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("events_not_list")

    declared_event_count = int(payload.get("event_count", -1))
    if declared_event_count != len(events):
        raise ValueError(f"event_count_mismatch:{declared_event_count}/{len(events)}")

    matured = 0
    pending_times: list[datetime] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError(f"event_{index}_not_object")
        matures_at = _parse_timestamp(event.get("matures_at"), f"event_{index}_matures_at")
        if matures_at <= checked_at:
            matured += 1
        else:
            pending_times.append(matures_at)

    declared_matured = int(payload.get("matured_event_count", -1))
    if declared_matured != matured:
        raise ValueError(f"matured_event_count_mismatch:{declared_matured}/{matured}")

    next_maturity = min(pending_times) if pending_times else None
    within_24h = sum(1 for value in pending_times if value <= checked_at + timedelta(hours=24))
    within_72h = sum(1 for value in pending_times if value <= checked_at + timedelta(hours=72))

    return {
        "research_only": True,
        "label_blind": True,
        "outcome_visible": False,
        "promotion_allowed": False,
        "checked_at": checked_at.isoformat(),
        "event_count": len(events),
        "matured_event_count": matured,
        "pending_maturity_event_count": len(pending_times),
        "next_maturity_at": next_maturity.isoformat() if next_maturity else None,
        "maturities_next_24h": within_24h,
        "maturities_next_72h": within_72h,
        "development_target_matured_events": 60,
        "validation_target_matured_events": 40,
        "development_maturity_count_ready": matured >= 60,
        "note": "Maturity timing is label-blind and does not authorize outcome access or live promotion.",
    }


def _get_json(base_url: str, path: str, api_key: str | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-Radar-Key"] = api_key
    request = Request(f"{base_url.rstrip('/')}{path}", headers=headers)
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "").strip()
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key:
        print("Missing production API configuration", file=sys.stderr)
        return 2

    version = _get_json(base_url, "/version")
    actual_sha = str(version.get("commit_sha") or "")
    if expected_sha and actual_sha != expected_sha:
        print(f"production_sha_mismatch:{actual_sha}/{expected_sha}", file=sys.stderr)
        return 1

    payload = _get_json(
        base_url,
        "/v1/research/swing-liquidity/forward-event-status",
        api_key,
    )
    try:
        summary = summarize_maturity_payload(payload)
    except (TypeError, ValueError) as exc:
        print(f"SWING_LIQUIDITY_MATURITY_STATUS_FAIL={exc}", file=sys.stderr)
        return 1

    summary["source_commit_sha"] = actual_sha
    print("SWING_LIQUIDITY_MATURITY_STATUS=" + json.dumps(summary, sort_keys=True))
    print("SWING LIQUIDITY LABEL-BLIND MATURITY STATUS VERIFIED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
