from scripts.production_policy_catalyst_event_store_smoke import run_smoke, validate_status


SHA = "abc123"


def _status(*, persisted=True):
    marker = {
        "status": "PERSISTED" if persisted else "UNAVAILABLE_MISSING_SOURCE_PUBLISHED_AT",
        "event_id": "event-1" if persisted else None,
        "spec_version": "policy-catalyst-event-store-v1",
    }
    event = {
        "published_at": "2026-08-21T12:00:00+00:00",
        "event_store_v1": marker,
    }
    return {
        "research_only": True,
        "context_only": True,
        "hard_gate": False,
        "live_strategy_mutated": False,
        "freshness": "FRESH",
        "latest_capture": {"source_commit_sha": SHA, "events": [event]},
        "recent_24h_events": [event],
    }


def test_validate_status_requires_timestamped_events_to_have_v1_marker():
    ok, reason, summary = validate_status(_status(), SHA)
    assert ok is True
    assert reason == "ok"
    assert summary["latest_v1_persisted_event_count"] == 1
    assert summary["recent_24h_v1_persisted_event_count"] == 1


def test_validate_status_fails_if_timestamped_event_was_not_dual_written():
    ok, reason, _ = validate_status(_status(persisted=False), SHA)
    assert ok is False
    assert reason == "timestamped_event_missing_v1_persistence"


def test_validate_status_can_prove_v1_from_recent_rows_when_latest_capture_has_no_events():
    status = _status()
    status["latest_capture"]["events"] = []
    ok, reason, summary = validate_status(status, SHA)
    assert ok is True
    assert reason == "ok"
    assert summary["latest_v1_persisted_event_count"] == 0
    assert summary["recent_24h_v1_persisted_event_count"] == 1


def test_run_smoke_is_read_only_and_checks_exact_sha():
    calls = []

    def fetch(url, api_key, timeout):
        calls.append((url, api_key, timeout))
        if url.endswith("/version"):
            return {"commit_sha": SHA}
        return _status()

    assert run_smoke("https://example.test", "secret", SHA, fetch=fetch) == 0
    assert [call[0] for call in calls] == [
        "https://example.test/version",
        "https://example.test/v1/research/policy-catalyst/status",
    ]
