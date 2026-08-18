from datetime import datetime, timedelta, timezone

from scripts.production_v073_prospective_funnel_smoke import validate_funnel_status


FEATURE_SHA = "f" * 40
WORKER_SHA = "a" * 40


def _day_payload() -> dict:
    now = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    return {
        "checked_at": now.isoformat(),
        "worker": {"source_commit_sha": WORKER_SHA},
        "prospective_funnel": {
            "status": "COMPLETE",
            "research_only": True,
            "label_free": True,
            "outcome_labels_stored": False,
            "spec_version": "v073-prospective-funnel-v1",
            "strategy_version": "0.7.3",
            "source_commit_sha": WORKER_SHA,
            "prospective_start_at": now.isoformat(),
            "current_run": {
                "observed_snapshots": 0,
                "inserted_snapshots": 0,
                "long_snapshots": 0,
                "short_snapshots": 0,
            },
            "cumulative": {
                "distinct_sweep_events": 0,
                "total_snapshots": 0,
                "exact_live_strict_trigger_events": 0,
                "symbols_observed": 0,
                "side_event_counts": {},
                "latest_gate_pass_counts": {},
                "latest_first_failed_gate_counts": {},
            },
        },
    }


def test_zero_sample_first_run_is_valid_when_contract_is_complete():
    now = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    result = validate_funnel_status(
        _day_payload(),
        feature_sha=FEATURE_SHA,
        now=now,
        ancestry_check=lambda feature, worker: feature == FEATURE_SHA and worker == WORKER_SHA,
    )
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["cumulative"]["distinct_sweep_events"] == 0


def test_stale_worker_status_fails_closed():
    now = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)
    payload = _day_payload()
    payload["checked_at"] = (now - timedelta(hours=2)).isoformat()
    result = validate_funnel_status(
        payload,
        feature_sha=FEATURE_SHA,
        now=now,
        ancestry_check=lambda *_: True,
    )
    assert result["ok"] is False
    assert "day-worker status is stale or missing checked_at" in result["errors"]


def test_old_worker_without_feature_fails_closed():
    result = validate_funnel_status(
        _day_payload(),
        feature_sha=FEATURE_SHA,
        now=datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc),
        ancestry_check=lambda *_: False,
    )
    assert result["ok"] is False
    assert "worker commit does not contain prospective-funnel feature SHA" in result["errors"]


def test_funnel_worker_sha_mismatch_fails_closed():
    payload = _day_payload()
    payload["prospective_funnel"]["source_commit_sha"] = "b" * 40
    result = validate_funnel_status(
        payload,
        feature_sha=FEATURE_SHA,
        now=datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc),
        ancestry_check=lambda *_: True,
    )
    assert result["ok"] is False
    assert "funnel source SHA does not match worker source SHA" in result["errors"]


def test_research_label_contract_fails_closed():
    payload = _day_payload()
    payload["prospective_funnel"]["label_free"] = False
    payload["prospective_funnel"]["outcome_labels_stored"] = True
    result = validate_funnel_status(
        payload,
        feature_sha=FEATURE_SHA,
        now=datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc),
        ancestry_check=lambda *_: True,
    )
    assert result["ok"] is False
    assert "label_free is not true" in result["errors"]
    assert "outcome_labels_stored is not false" in result["errors"]
