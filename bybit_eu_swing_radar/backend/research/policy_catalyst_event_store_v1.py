"""Research-only persistence/freshness contract for policy catalyst events.

This module deliberately performs no network I/O and cannot mutate live strategy,
score, ranking, eligibility, or execution. It normalizes already-fetched primary
source events into a point-in-time record with immutable source provenance and
collector first-seen timestamps.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from research.policy_catalyst_sources_v1 import classify_primary_policy_url

SPEC_VERSION = "policy-catalyst-event-store-v1"
DEFAULT_FRESHNESS_MINUTES = 30

EVENT_SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS policy_catalyst_event_v1 (
    event_id TEXT PRIMARY KEY,
    spec_version TEXT NOT NULL,
    provider_code TEXT NOT NULL,
    authority_tier TEXT NOT NULL,
    event_class TEXT NOT NULL,
    headline TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    source_published_at TIMESTAMPTZ NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    source_role TEXT NOT NULL,
    provenance JSONB NOT NULL,
    context_only BOOLEAN NOT NULL DEFAULT TRUE,
    hard_gate BOOLEAN NOT NULL DEFAULT FALSE,
    score_mutation BOOLEAN NOT NULL DEFAULT FALSE,
    ranking_mutation BOOLEAN NOT NULL DEFAULT FALSE,
    eligibility_mutation BOOLEAN NOT NULL DEFAULT FALSE,
    execution_mutation BOOLEAN NOT NULL DEFAULT FALSE,
    trade_direction TEXT,
    causal_attribution TEXT NOT NULL DEFAULT 'UNCONFIRMED_CONTEXT_ONLY'
);

CREATE INDEX IF NOT EXISTS idx_policy_catalyst_event_v1_first_seen
    ON policy_catalyst_event_v1 (first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_policy_catalyst_event_v1_source_published
    ON policy_catalyst_event_v1 (source_published_at DESC);
CREATE INDEX IF NOT EXISTS idx_policy_catalyst_event_v1_class
    ON policy_catalyst_event_v1 (event_class, first_seen_at DESC);
"""


def _utc(value: datetime | str, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise ValueError(f"{field} is required")
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _event_id(provider_code: str, canonical_url: str, source_published_at: datetime) -> str:
    raw = f"{provider_code}|{canonical_url}|{source_published_at.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_policy_event(
    raw: Mapping[str, Any],
    *,
    observed_at: datetime | str,
) -> dict[str, Any]:
    """Normalize an already-fetched official-source event without trading semantics."""
    url = str(raw.get("url") or raw.get("canonical_url") or "").strip()
    classified = classify_primary_policy_url(url)
    if classified is None:
        raise ValueError("unsupported or non-primary policy source url")

    headline = str(raw.get("headline") or raw.get("title") or "").strip()
    if not headline:
        raise ValueError("headline is required")

    published_at = _utc(raw.get("source_published_at") or raw.get("published_at"), "source_published_at")
    first_seen = _utc(observed_at, "observed_at")
    if first_seen < published_at:
        # Provider clocks can be imprecise, but a collector timestamp predating the
        # published timestamp would make event-age semantics ambiguous. Fail closed.
        raise ValueError("first_seen_at cannot precede source_published_at")

    requested_class = raw.get("event_class")
    allowed_classes = set(classified.get("event_classes") or [])
    event_class = str(requested_class or classified.get("event_class") or "").strip()
    if not event_class or event_class not in allowed_classes:
        raise ValueError("event_class is not registered for source")

    provider_code = str(classified["provider_code"])
    event_id = _event_id(provider_code, url, published_at)
    provenance = {
        "provider": classified["provider"],
        "provider_code": provider_code,
        "authority_tier": classified["authority_tier"],
        "source_family": classified["source_family"],
        "canonical_url": url,
        "source_published_at": published_at.isoformat(),
        "first_seen_at": first_seen.isoformat(),
        "collector_clock": "LOCAL_UTC",
    }
    return {
        "event_id": event_id,
        "spec_version": SPEC_VERSION,
        "provider_code": provider_code,
        "authority_tier": classified["authority_tier"],
        "event_class": event_class,
        "headline": headline,
        "canonical_url": url,
        "source_published_at": published_at.isoformat(),
        "first_seen_at": first_seen.isoformat(),
        "last_seen_at": first_seen.isoformat(),
        "source_role": "PRIMARY_SOURCE",
        "provenance": provenance,
        "context_only": True,
        "hard_gate": False,
        "score_mutation": False,
        "ranking_mutation": False,
        "eligibility_mutation": False,
        "execution_mutation": False,
        "trade_direction": None,
        "causal_attribution": "UNCONFIRMED_CONTEXT_ONLY",
    }


def freshness_status(
    event: Mapping[str, Any],
    *,
    as_of: datetime | str,
    freshness_minutes: int = DEFAULT_FRESHNESS_MINUTES,
) -> dict[str, Any]:
    """Return visible collector freshness; freshness is context, never a hard gate."""
    if freshness_minutes <= 0:
        raise ValueError("freshness_minutes must be positive")
    now = _utc(as_of, "as_of")
    first_seen = _utc(event.get("first_seen_at"), "first_seen_at")
    age_seconds = max(0.0, (now - first_seen).total_seconds())
    fresh = age_seconds <= freshness_minutes * 60
    return {
        "status": "FRESH" if fresh else "STALE",
        "age_seconds": age_seconds,
        "freshness_minutes": freshness_minutes,
        "available": True,
        "context_only": True,
        "hard_gate": False,
    }


def availability_status(
    *,
    checked_at: datetime | str,
    last_success_at: datetime | str | None,
    error: str | None = None,
) -> dict[str, Any]:
    """Expose source-layer availability without converting it into trade eligibility."""
    checked = _utc(checked_at, "checked_at")
    last_success = None if last_success_at is None else _utc(last_success_at, "last_success_at")
    available = last_success is not None and not error
    return {
        "status": "AVAILABLE" if available else "UNAVAILABLE",
        "checked_at": checked.isoformat(),
        "last_success_at": None if last_success is None else last_success.isoformat(),
        "reason": None if available else (str(error).strip() or "NO_SUCCESSFUL_COLLECTION"),
        "context_only": True,
        "hard_gate": False,
        "score_mutation": False,
        "ranking_mutation": False,
        "eligibility_mutation": False,
        "execution_mutation": False,
    }


def canonical_json(event: Mapping[str, Any]) -> str:
    """Stable serialization helper for deterministic persistence/regression tests."""
    return json.dumps(dict(event), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
