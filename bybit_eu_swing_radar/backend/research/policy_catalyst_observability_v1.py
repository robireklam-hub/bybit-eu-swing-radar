"""Compact per-source observability for the research-only policy catalyst feed.

This module is deterministic and performs no network or trading I/O. It combines
latest collector source results with persisted event-store-v1 aggregates so the
research status surface can distinguish source availability, collector freshness,
and natural absence of timestamped v1 events without turning any of them into a
trading gate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from research.policy_catalyst_event_store_v1 import SPEC_VERSION as EVENT_STORE_SPEC_VERSION
from research.policy_catalyst_sources_v1 import source_registry

SPEC_VERSION = "policy-catalyst-source-observability-v1"
DEFAULT_CAPTURE_FRESH_SECONDS = 30 * 60


def _utc(value: datetime | str | None) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(as_of: datetime, value: datetime | str | None) -> float | None:
    parsed = _utc(value)
    if parsed is None:
        return None
    return max(0.0, (as_of - parsed).total_seconds())


def build_source_observability(
    *,
    latest_capture: Mapping[str, Any] | None,
    event_store_rows: Iterable[Mapping[str, Any]],
    as_of: datetime | str,
    capture_fresh_seconds: int = DEFAULT_CAPTURE_FRESH_SECONDS,
) -> dict[str, Any]:
    """Return one compact, non-gating health row per registered primary source."""
    if capture_fresh_seconds <= 0:
        raise ValueError("capture_fresh_seconds must be positive")
    now = _utc(as_of)
    if now is None:
        raise ValueError("as_of is required")

    capture = dict(latest_capture or {})
    source_results = {
        str(row.get("provider_code") or ""): dict(row)
        for row in (capture.get("source_results") or [])
        if isinstance(row, Mapping) and row.get("provider_code")
    }
    persisted = {
        str(row.get("provider_code") or ""): dict(row)
        for row in event_store_rows
        if isinstance(row, Mapping) and row.get("provider_code")
    }

    rows: list[dict[str, Any]] = []
    for source in source_registry():
        code = str(source["provider_code"])
        enabled = bool(source.get("enabled"))
        source_result = source_results.get(code)
        store_row = persisted.get(code)
        capture_at = None if source_result is None else source_result.get("captured_at")
        capture_age = _age_seconds(now, capture_at)
        capture_freshness = (
            "FRESH"
            if capture_age is not None and capture_age <= capture_fresh_seconds
            else "STALE"
            if capture_age is not None
            else "UNAVAILABLE"
        )
        collection_ok = bool(source_result and source_result.get("status") == "OK")
        collection_status = (
            "NOT_CONFIGURED"
            if not enabled
            else "AVAILABLE"
            if collection_ok
            else "UNAVAILABLE"
        )
        event_count = int((store_row or {}).get("event_count") or 0)
        latest_last_seen = (store_row or {}).get("latest_last_seen_at")
        event_age = _age_seconds(now, latest_last_seen)
        event_freshness = (
            "FRESH"
            if event_age is not None and event_age <= capture_fresh_seconds
            else "STALE"
            if event_age is not None
            else "NO_EVENT"
        )
        if not enabled:
            integration_status = "NOT_CONFIGURED"
        elif not collection_ok:
            integration_status = "UNAVAILABLE_SOURCE_COLLECTION"
        elif event_count > 0:
            integration_status = "PERSISTED_EVENT_OBSERVED"
        else:
            integration_status = "PENDING_NO_TIMESTAMPED_EVENT"

        rows.append(
            {
                "provider": source["provider"],
                "provider_code": code,
                "enabled": enabled,
                "authority_tier": source["authority_tier"],
                "collection_status": collection_status,
                "collection_freshness": capture_freshness,
                "collection_age_seconds": None if capture_age is None else round(capture_age, 3),
                "collection_error": None if source_result is None else source_result.get("error"),
                "event_store_status": integration_status,
                "event_store_spec_version": EVENT_STORE_SPEC_VERSION,
                "event_store_event_count": event_count,
                "event_store_latest_first_seen_at": (store_row or {}).get("latest_first_seen_at"),
                "event_store_latest_last_seen_at": latest_last_seen,
                "event_store_event_freshness": event_freshness,
                "event_store_event_age_seconds": None if event_age is None else round(event_age, 3),
                "context_only": True,
                "hard_gate": False,
                "score_mutation": False,
                "ranking_mutation": False,
                "eligibility_mutation": False,
                "execution_mutation": False,
            }
        )

    enabled_rows = [row for row in rows if row["enabled"]]
    return {
        "spec_version": SPEC_VERSION,
        "event_store_spec_version": EVENT_STORE_SPEC_VERSION,
        "as_of": now.isoformat(),
        "capture_fresh_seconds": capture_fresh_seconds,
        "enabled_source_count": len(enabled_rows),
        "available_source_count": sum(row["collection_status"] == "AVAILABLE" for row in enabled_rows),
        "fresh_source_count": sum(row["collection_freshness"] == "FRESH" for row in enabled_rows),
        "persisted_event_source_count": sum(row["event_store_event_count"] > 0 for row in enabled_rows),
        "sources": rows,
        "research_only": True,
        "context_only": True,
        "hard_gate": False,
        "live_strategy_mutated": False,
    }
