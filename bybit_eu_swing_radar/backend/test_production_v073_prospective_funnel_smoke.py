from datetime import datetime, timedelta, timezone

from scripts.production_v073_prospective_funnel_smoke import validate_standalone_status


SHA = "a" * 40


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
    }


def test_zero_sample_standalone_first_run_is_valid():
    now = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    result = validate_standalone_status(_payload(), SHA, now=now)
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["cumulative"]["distinct_sweep_events"] == 0


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
