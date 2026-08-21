from datetime import datetime, timedelta, timezone

from scripts.production_v073_prospective_funnel_smoke import (
    validate_barrier_observer_status,
    validate_barrier_parent_status,
    validate_standalone_status,
)


SHA = "a" * 40
NOW = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
LOCKED = "LOCKED_UNTIL_PREREGISTERED_DEVELOPMENT_GATE"


def _payload() -> dict:
    return {
        "status": "COMPLETE",
        "research_only": True,
        "label_free": True,
        "outcome_labels_stored": False,
        "spec_version": "v073-prospective-funnel-v1",
        "strategy_version": "0.7.3",
        "source_commit_sha": SHA,
        "prospective_start_at": NOW.isoformat(),
        "captured_at": NOW.isoformat(),
        "execution_mode": "STANDALONE_RAILWAY_CRON",
        "live_worker_inline_recorder": False,
        "live_worker_mutation": False,
        "authoritative_live_scan_as_of": NOW.isoformat(),
        "authoritative_live_strict_setups": 0,
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
    }


def _parent_payload() -> dict:
    return {
        "status": "COMPLETE",
        "research_only": True,
        "label_free": True,
        "execution_authorized": False,
        "live_strategy_mutation": False,
        "parent_strategy_version": "0.7.5",
        "source_commit_sha": SHA,
        "prospective_start_at": NOW.isoformat(),
        "captured_at": NOW.isoformat(),
        "admitted_this_run": 0,
        "inserted_this_run": 0,
        "total_frozen_parents": 0,
        "outcome_visibility": LOCKED,
        "execution_mode": "SHARED_STANDALONE_RESEARCH_SIDECAR",
        "current_live_strategy_version": "0.7.6",
        "live_worker_inline_recorder": False,
        "live_worker_mutation": False,
        "forced_tracking_symbols": [],
    }


def _observer_payload() -> dict:
    return {
        "status": "COMPLETE",
        "study": "day-barrier-clear-rearm-v1",
        "observer_version": "day-barrier-clear-observer-v1",
        "research_only": True,
        "label_free": True,
        "execution_authorized": False,
        "live_strategy_mutation": False,
        "score_mutation": False,
        "ranking_mutation": False,
        "eligibility_mutation": False,
        "execution_mutation": False,
        "parent_strategy_version": "0.7.5",
        "source_commit_sha": SHA,
        "captured_at": NOW.isoformat(),
        "resolved_this_run": {},
        "pending_without_analysis_this_run": 0,
        "cumulative": {
            "pending": 0,
            "cleared": 0,
            "invalidated_boundary": 0,
            "invalidated_structure": 0,
        },
        "outcome_visibility": LOCKED,
        "execution_mode": "SHARED_STANDALONE_RESEARCH_SIDECAR",
        "current_live_strategy_version": "0.7.6",
        "live_worker_inline_recorder": False,
        "live_worker_mutation": False,
        "forced_tracking_symbols": [],
    }


def test_zero_sample_standalone_first_run_is_valid():
    result = validate_standalone_status(_payload(), SHA, now=NOW)
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["cumulative"]["distinct_sweep_events"] == 0


def test_zero_parent_and_zero_resolution_are_valid_prospective_states():
    parent = validate_barrier_parent_status(_parent_payload(), SHA, now=NOW)
    observer = validate_barrier_observer_status(_observer_payload(), SHA, now=NOW)
    assert parent["ok"] is True
    assert parent["total_frozen_parents"] == 0
    assert observer["ok"] is True
    assert observer["cumulative"]["cleared"] == 0


def test_barrier_contract_fails_closed_on_live_mutation_or_execution_authorization():
    parent_payload = _parent_payload()
    parent_payload["execution_authorized"] = True
    parent_payload["live_worker_mutation"] = True
    parent = validate_barrier_parent_status(parent_payload, SHA, now=NOW)
    assert parent["ok"] is False
    assert "parent.execution_authorized mismatch" in parent["errors"]
    assert "parent.live_worker_mutation mismatch" in parent["errors"]

    observer_payload = _observer_payload()
    observer_payload["execution_authorized"] = True
    observer_payload["score_mutation"] = True
    observer = validate_barrier_observer_status(observer_payload, SHA, now=NOW)
    assert observer["ok"] is False
    assert "observer.execution_authorized mismatch" in observer["errors"]
    assert "observer.score_mutation mismatch" in observer["errors"]


def test_barrier_status_requires_exact_sidecar_sha():
    parent = _parent_payload()
    parent["source_commit_sha"] = "b" * 40
    observer = _observer_payload()
    observer["source_commit_sha"] = "b" * 40
    assert "parent source SHA mismatch" in validate_barrier_parent_status(parent, SHA, now=NOW)["errors"]
    assert "observer source SHA mismatch" in validate_barrier_observer_status(observer, SHA, now=NOW)["errors"]


def test_stale_standalone_capture_fails_closed():
    now = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)
    payload = _payload()
    payload["captured_at"] = (now - timedelta(hours=2)).isoformat()
    result = validate_standalone_status(payload, SHA, now=now)
    assert result["ok"] is False
    assert "standalone capture stale or missing" in result["errors"]


def test_wrong_standalone_sha_fails_closed():
    payload = _payload()
    payload["source_commit_sha"] = "b" * 40
    result = validate_standalone_status(payload, SHA, now=NOW)
    assert result["ok"] is False
    assert "standalone source SHA mismatch" in result["errors"]


def test_research_label_contract_fails_closed():
    payload = _payload()
    payload["label_free"] = False
    payload["outcome_labels_stored"] = True
    result = validate_standalone_status(payload, SHA, now=NOW)
    assert result["ok"] is False
    assert "label_free mismatch" in result["errors"]
    assert "outcome_labels_stored mismatch" in result["errors"]


def test_live_mutation_or_inline_mode_fails_closed():
    payload = _payload()
    payload["live_worker_inline_recorder"] = True
    payload["live_worker_mutation"] = True
    result = validate_standalone_status(payload, SHA, now=NOW)
    assert result["ok"] is False
    assert "live_worker_inline_recorder mismatch" in result["errors"]
    assert "live_worker_mutation mismatch" in result["errors"]
