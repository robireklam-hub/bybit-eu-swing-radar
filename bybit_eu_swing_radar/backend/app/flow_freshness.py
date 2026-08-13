from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


FLOW_MAX_AGE_SECONDS = 300.0
STALE_COVERAGE_STATUS = "STALE_FLOW_CONTEXT"


def apply_flow_freshness(
    payload: dict[str, Any],
    *,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """Return a freshness-adjusted copy of a cached Flow payload."""
    result = deepcopy(payload)
    now = _as_utc(reference_time or datetime.now(timezone.utc))
    timestamp = _parse_timestamp(result.get("data_as_of"))
    age = None if timestamp is None else max((now - timestamp).total_seconds(), 0.0)
    if age is not None and age <= FLOW_MAX_AGE_SECONDS:
        return result

    result["data_quality"] = "DEGRADED"
    if result.get("coverage_status") == "GOOD":
        result["coverage_status"] = STALE_COVERAGE_STATUS
    notes = list(result.get("notes") or [])
    note = "Cached Flow context is stale or has no valid canonical data_as_of timestamp."
    if note not in notes:
        notes.append(note)
    result["notes"] = notes
    return result


def summarize_flow_payloads(
    payloads: list[dict[str, Any] | None],
    *,
    flow_batch_id: str,
    reference_time: datetime | None = None,
) -> dict[str, int]:
    counts = {"good": 0, "partial": 0, "no_derivative_match": 0}
    for payload in payloads:
        if payload is None:
            counts["partial"] += 1
            continue
        if payload.get("flow_batch_id") != flow_batch_id:
            counts["partial"] += 1
            continue
        coverage = apply_flow_freshness(payload, reference_time=reference_time).get("coverage_status")
        if coverage == "GOOD":
            counts["good"] += 1
        elif coverage == "NO_BYBIT_GLOBAL_LINEAR_PERPETUAL_MATCH":
            counts["no_derivative_match"] += 1
        else:
            counts["partial"] += 1
    return counts


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    try:
        return _as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
