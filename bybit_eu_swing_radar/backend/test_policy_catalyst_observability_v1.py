from datetime import datetime, timedelta, timezone

from research.policy_catalyst_observability_v1 import build_source_observability


def _source(result, code):
    return next(row for row in result["sources"] if row["provider_code"] == code)


def test_source_observability_distinguishes_collection_health_from_natural_event_absence():
    now = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)
    capture = {
        "source_results": [
            {
                "provider_code": "SEC",
                "status": "OK",
                "captured_at": (now - timedelta(minutes=5)).isoformat(),
                "event_count": 0,
            },
            {
                "provider_code": "FED",
                "status": "ERROR",
                "error": "ReadTimeout",
                "captured_at": (now - timedelta(minutes=5)).isoformat(),
                "event_count": 0,
            },
        ]
    }

    result = build_source_observability(latest_capture=capture, event_store_rows=[], as_of=now)

    sec = _source(result, "SEC")
    assert sec["collection_status"] == "AVAILABLE"
    assert sec["collection_freshness"] == "FRESH"
    assert sec["event_store_status"] == "PENDING_NO_TIMESTAMPED_EVENT"
    assert sec["event_store_event_freshness"] == "NO_EVENT"

    fed = _source(result, "FED")
    assert fed["collection_status"] == "UNAVAILABLE"
    assert fed["collection_error"] == "ReadTimeout"
    assert fed["event_store_status"] == "UNAVAILABLE_SOURCE_COLLECTION"

    congress = _source(result, "CONGRESS")
    assert congress["enabled"] is False
    assert congress["collection_status"] == "NOT_CONFIGURED"
    assert congress["event_store_status"] == "NOT_CONFIGURED"


def test_source_observability_reports_persisted_v1_event_without_direction_or_gate_semantics():
    now = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)
    capture = {
        "source_results": [
            {
                "provider_code": "TREASURY",
                "status": "OK",
                "captured_at": (now - timedelta(minutes=3)).isoformat(),
            }
        ]
    }
    event_rows = [
        {
            "provider_code": "TREASURY",
            "event_count": 3,
            "latest_first_seen_at": (now - timedelta(minutes=15)).isoformat(),
            "latest_last_seen_at": (now - timedelta(minutes=2)).isoformat(),
        }
    ]

    result = build_source_observability(
        latest_capture=capture,
        event_store_rows=event_rows,
        as_of=now,
    )
    treasury = _source(result, "TREASURY")

    assert treasury["event_store_status"] == "PERSISTED_EVENT_OBSERVED"
    assert treasury["event_store_event_count"] == 3
    assert treasury["event_store_event_freshness"] == "FRESH"
    assert treasury["context_only"] is True
    assert treasury["hard_gate"] is False
    assert treasury["score_mutation"] is False
    assert treasury["ranking_mutation"] is False
    assert treasury["eligibility_mutation"] is False
    assert treasury["execution_mutation"] is False
    assert result["live_strategy_mutated"] is False


def test_source_observability_marks_old_collection_and_event_stale_without_hard_gate():
    now = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)
    capture = {
        "source_results": [
            {
                "provider_code": "SEC",
                "status": "OK",
                "captured_at": (now - timedelta(hours=2)).isoformat(),
            }
        ]
    }
    event_rows = [
        {
            "provider_code": "SEC",
            "event_count": 1,
            "latest_first_seen_at": (now - timedelta(hours=4)).isoformat(),
            "latest_last_seen_at": (now - timedelta(hours=2)).isoformat(),
        }
    ]

    result = build_source_observability(latest_capture=capture, event_store_rows=event_rows, as_of=now)
    sec = _source(result, "SEC")

    assert sec["collection_status"] == "AVAILABLE"
    assert sec["collection_freshness"] == "STALE"
    assert sec["event_store_status"] == "PERSISTED_EVENT_OBSERVED"
    assert sec["event_store_event_freshness"] == "STALE"
    assert sec["hard_gate"] is False
