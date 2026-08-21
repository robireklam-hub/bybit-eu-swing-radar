from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts import preflight_v073_prospective_funnel as preflight


def _barrier_payload(sha: str) -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "status": "COMPLETE",
        "research_only": True,
        "label_free": True,
        "outcome_labels_stored": False,
        "outcome_visibility": "LOCKED_UNTIL_PREREGISTERED_DEVELOPMENT_GATE",
        "spec_version": "day-barrier-clear-recorder-v1",
        "study_id": "day-barrier-clear-rearm-v1",
        "parent_strategy_version": "0.7.5",
        "execution_mode": "SHARED_STANDALONE_RESEARCH_SIDECAR",
        "live_worker_mutation": False,
        "score_mutation": False,
        "ranking_mutation": False,
        "eligibility_mutation": False,
        "execution_mutation": False,
        "derivatives_context_only": True,
        "source_commit_sha": sha,
        "captured_at": now,
        "prospective_start_at": "2026-08-21T00:00:00+00:00",
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


def _prospective_payload(sha: str) -> dict[str, object]:
    return {
        "status": "COMPLETE",
        "research_only": True,
        "label_free": True,
        "outcome_labels_stored": False,
        "spec_version": "v073-prospective-funnel-v1",
        "strategy_version": "0.7.3",
        "execution_mode": "STANDALONE_RAILWAY_CRON",
        "live_worker_inline_recorder": False,
        "live_worker_mutation": False,
        "source_commit_sha": sha,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "prospective_start_at": "2026-08-15T00:00:00+00:00",
        "current_run": {
            "observed_snapshots": 30,
            "inserted_snapshots": 30,
            "long_snapshots": 15,
            "short_snapshots": 15,
        },
        "cumulative": {
            "distinct_sweep_events": 10,
            "total_snapshots": 100,
            "exact_live_strict_trigger_events": 0,
            "symbols_observed": 10,
            "side_event_counts": {},
            "latest_gate_pass_counts": {},
            "latest_first_failed_gate_counts": {},
        },
        "barrier_clear_rearm": _barrier_payload(sha),
    }


def _live_status() -> dict[str, object]:
    return {
        "prospective_funnel": {
            "status": "EXTERNALIZED",
            "enabled": False,
            "reason": "STANDALONE_RECORDER_OWNS_CAPTURE",
            "execution_mode": "STANDALONE_RAILWAY_CRON",
        }
    }


def test_exact_main_fresh_capture_passes_preflight():
    sha = "a" * 40
    result = preflight.evaluate_preflight(
        version={"commit_sha": sha},
        live_status=_live_status(),
        prospective_status=_prospective_payload(sha),
        expected_sha=sha,
    )

    assert result["ok"] is True
    assert result["errors"] == []


def test_stale_source_sha_fails_preflight_even_when_api_is_exact():
    expected = "a" * 40
    result = preflight.evaluate_preflight(
        version={"commit_sha": expected},
        live_status=_live_status(),
        prospective_status=_prospective_payload("b" * 40),
        expected_sha=expected,
    )

    assert result["ok"] is False
    assert "standalone source SHA mismatch" in result["errors"]
    assert "barrier_clear_rearm source SHA mismatch" in result["errors"]


def test_non_externalized_live_worker_fails_preflight():
    sha = "a" * 40
    live = _live_status()
    live["prospective_funnel"] = {
        "status": "INLINE",
        "enabled": True,
        "reason": "WRONG_OWNER",
        "execution_mode": "INLINE",
    }

    result = preflight.evaluate_preflight(
        version={"commit_sha": sha},
        live_status=live,
        prospective_status=_prospective_payload(sha),
        expected_sha=sha,
    )

    assert result["ok"] is False
    assert "prospective_funnel.status mismatch" in result["errors"]
    assert "prospective_funnel.enabled mismatch" in result["errors"]


def test_api_sha_mismatch_fails_preflight():
    expected = "a" * 40
    result = preflight.evaluate_preflight(
        version={"commit_sha": "b" * 40},
        live_status=_live_status(),
        prospective_status=_prospective_payload(expected),
        expected_sha=expected,
    )

    assert result["ok"] is False
    assert "production API SHA mismatch" in result["errors"]


def test_wait_for_preflight_allows_normal_auto_deploy_settling():
    expected = "a" * 40
    calls = {"version": 0}

    def fetch(path: str, _auth: bool) -> dict[str, object]:
        if path == "/version":
            calls["version"] += 1
            return {"commit_sha": expected if calls["version"] >= 3 else "b" * 40}
        if path == "/v1/day-trade/status":
            return _live_status()
        if path == "/v1/day-trade/research/prospective-funnel/status":
            return _prospective_payload(expected)
        raise AssertionError(path)

    result = preflight.wait_for_preflight(
        fetch,
        expected_sha=expected,
        max_attempts=4,
        sleep_seconds=0,
    )

    assert result["ok"] is True
    assert calls["version"] == 3


def test_direct_script_import_path_works_outside_backend_cwd(tmp_path):
    backend_root = Path(__file__).resolve().parent
    script = backend_root / "scripts" / "preflight_v073_prospective_funnel.py"
    code = "import runpy; runpy.run_path(" + repr(str(script)) + ", run_name='preflight_import_check')"

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr
