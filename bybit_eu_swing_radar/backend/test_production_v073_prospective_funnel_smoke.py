from datetime import datetime, timedelta, timezone

from scripts.production_v073_prospective_funnel_smoke import validate_standalone_status


SHA = "a" * 40


def _barrier(now: datetime) -> dict:
    return {
        "status": "COMPLETE",
        "research_only": True,
        "label_free": True,
        "outcome_labels_stored": False,
        "outcome_visibility": "LOCKED_UNTIL_PREREGISTERED_DEVELOPMENT_GATE",
        "spec_version": "day-barrier-clear-recorder-v1",
        "study_id": "day-barrier-clear-rearm-v1",
        "parent_strategy_version": "0.7.5",
        "prospective_start_at": now.isoformat(),
        "captured_at": now.isoformat(),
        "source_commit_sha": SHA,
        "execution_mode": "SHARED_STANDALONE_RESEARCH_SIDECAR",
        "live_worker_mutation": False,
        "score_mutation": False,
        "ranking_mutation": False,
        "eligibility_mutation": False,
        "execution_mutation": False,
        "derivatives_context_only": True,
        "current_run": {
            "eligible_parent_candidates": 0,
            "inserted_new_parents": 0,
            "resolved_cleared": 0,
            "resolved_boundary_invalidations": 0,
            "resolved_structure_invalidations": 0,
            "forced_tracking_symbols": [],
        },
        "cumulative": {
            "parent_events": 0,
            "pending_parents": 0,
            "cleared_parents": 0,
            "boundary_invalidated_parents": 0,
            "structure_invalidated_parents": 0,
            "clear_rows": 0,
            "side_parent_counts": {},
            "symbols_observed": 0,
            "symbol_list": [],
        },
    }


def _payload() -> dict:
    now = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    return {
        "status": "COMPLETE",
        "research_only": True,
        "label_free": True,
        "outcome_labels_stored": False,
        "spec_version": "v073-prospective-funnel-v1",
        "strategy_version": "0.7.3",
        "source_commit_sha": SHA,
        "prospective_start_at": now.isoformat(),
        "captured_at": now.isoformat(),
        "execution_mode": "STANDALONE_RAILWAY_CRON",
        "live_worker_inline_recorder": False,
        "live_worker_mutation": False,
        "authoritative_live_scan_as_of": now.isoformat(),
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
        "barrier_clear_rearm": _barrier(now),
    }


def test_zero_sample_standalone_first_run_is_valid():
    now = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    result = validate_standalone_status(_payload(), SHA, now=now)
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["cumulative"]["distinct_sweep_events"] == 0
    assert result["barrier_clear_rearm"]["cumulative"]["parent_events"] == 0


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
    result = validate_standalone_status(
        payload, SHA, now=datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    )
    assert result["ok"] is False
    assert "standalone source SHA mismatch" in result["errors"]


def test_research_label_contract_fails_closed():
    payload = _payload()
    payload["label_free"] = False
    payload["outcome_labels_stored"] = True
    result = validate_standalone_status(
        payload, SHA, now=datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    )
    assert result["ok"] is False
    assert "label_free mismatch" in result["errors"]
    assert "outcome_labels_stored mismatch" in result["errors"]


def test_live_mutation_or_inline_mode_fails_closed():
    payload = _payload()
    payload["live_worker_inline_recorder"] = True
    payload["live_worker_mutation"] = True
    result = validate_standalone_status(
        payload, SHA, now=datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    )
    assert result["ok"] is False
    assert "live_worker_inline_recorder mismatch" in result["errors"]
    assert "live_worker_mutation mismatch" in result["errors"]


def test_barrier_recorder_missing_or_wrong_version_fails_closed():
    now = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    payload = _payload()
    payload.pop("barrier_clear_rearm")
    result = validate_standalone_status(payload, SHA, now=now)
    assert result["ok"] is False
    assert "barrier_clear_rearm missing" in result["errors"]

    payload = _payload()
    payload["barrier_clear_rearm"]["parent_strategy_version"] = "0.7.6"
    result = validate_standalone_status(payload, SHA, now=now)
    assert result["ok"] is False
    assert "barrier_clear_rearm.parent_strategy_version mismatch" in result["errors"]


def test_barrier_recorder_outcome_or_mutation_contract_fails_closed():
    now = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    payload = _payload()
    payload["barrier_clear_rearm"]["outcome_labels_stored"] = True
    payload["barrier_clear_rearm"]["execution_mutation"] = True
    result = validate_standalone_status(payload, SHA, now=now)
    assert result["ok"] is False
    assert "barrier_clear_rearm.outcome_labels_stored mismatch" in result["errors"]
    assert "barrier_clear_rearm.execution_mutation mismatch" in result["errors"]
