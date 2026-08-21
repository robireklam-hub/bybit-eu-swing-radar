from datetime import datetime, timezone

import pytest

from research.policy_catalyst_event_store_v1 import (
    EVENT_SCHEMA_SQL,
    availability_status,
    canonical_json,
    freshness_status,
    normalize_policy_event,
)
from research.policy_catalyst_sources_v1 import SEC_2026_76_FIXTURE_URL


def _sec_event():
    return {
        "url": SEC_2026_76_FIXTURE_URL,
        "headline": "SEC Proposes New Regulation Crypto Assets",
        "source_published_at": "2026-08-18T14:00:00Z",
        "event_class": "US_CRYPTO_REGULATION",
    }


def test_normalized_primary_event_preserves_timestamp_and_non_mutation_contract():
    event = normalize_policy_event(_sec_event(), observed_at="2026-08-18T14:03:00Z")
    assert event["provider_code"] == "SEC"
    assert event["source_published_at"] == "2026-08-18T14:00:00+00:00"
    assert event["first_seen_at"] == "2026-08-18T14:03:00+00:00"
    assert event["last_seen_at"] == event["first_seen_at"]
    assert event["context_only"] is True
    assert event["hard_gate"] is False
    assert event["score_mutation"] is False
    assert event["ranking_mutation"] is False
    assert event["eligibility_mutation"] is False
    assert event["execution_mutation"] is False
    assert event["trade_direction"] is None
    assert event["causal_attribution"] == "UNCONFIRMED_CONTEXT_ONLY"
    assert event["provenance"]["canonical_url"] == SEC_2026_76_FIXTURE_URL


def test_event_id_and_serialization_are_deterministic_for_same_point_in_time_event():
    first = normalize_policy_event(_sec_event(), observed_at="2026-08-18T14:03:00Z")
    second = normalize_policy_event(_sec_event(), observed_at="2026-08-18T14:03:00+00:00")
    assert first["event_id"] == second["event_id"]
    assert canonical_json(first) == canonical_json(second)


def test_unregistered_source_or_event_class_fails_closed():
    bad_url = _sec_event()
    bad_url["url"] = "https://example.com/news/crypto"
    with pytest.raises(ValueError, match="non-primary"):
        normalize_policy_event(bad_url, observed_at="2026-08-18T14:03:00Z")

    bad_class = _sec_event()
    bad_class["event_class"] = "TREASURY_DEBT_MANAGEMENT"
    with pytest.raises(ValueError, match="event_class"):
        normalize_policy_event(bad_class, observed_at="2026-08-18T14:03:00Z")


def test_first_seen_cannot_precede_source_publish_time():
    with pytest.raises(ValueError, match="cannot precede"):
        normalize_policy_event(_sec_event(), observed_at="2026-08-18T13:59:59Z")


def test_freshness_is_visible_context_not_trade_gate():
    event = normalize_policy_event(_sec_event(), observed_at="2026-08-18T14:03:00Z")
    fresh = freshness_status(event, as_of="2026-08-18T14:20:00Z", freshness_minutes=30)
    stale = freshness_status(event, as_of="2026-08-18T15:00:00Z", freshness_minutes=30)
    assert fresh["status"] == "FRESH"
    assert stale["status"] == "STALE"
    assert fresh["hard_gate"] is False
    assert stale["hard_gate"] is False


def test_unavailable_source_status_fails_visible_but_not_trade_eligibility():
    status = availability_status(
        checked_at=datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc),
        last_success_at=None,
        error="collector timeout",
    )
    assert status["status"] == "UNAVAILABLE"
    assert status["reason"] == "collector timeout"
    assert status["context_only"] is True
    assert status["hard_gate"] is False
    assert status["score_mutation"] is False
    assert status["ranking_mutation"] is False
    assert status["eligibility_mutation"] is False
    assert status["execution_mutation"] is False


def test_schema_persists_required_provenance_and_forbids_direction_default():
    for column in (
        "source_published_at TIMESTAMPTZ NOT NULL",
        "first_seen_at TIMESTAMPTZ NOT NULL",
        "last_seen_at TIMESTAMPTZ NOT NULL",
        "provenance JSONB NOT NULL",
        "context_only BOOLEAN NOT NULL DEFAULT TRUE",
        "hard_gate BOOLEAN NOT NULL DEFAULT FALSE",
        "trade_direction TEXT",
    ):
        assert column in EVENT_SCHEMA_SQL
