from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts import preflight_v073_prospective_funnel as preflight


LOCKED = "LOCKED_UNTIL_PREREGISTERED_DEVELOPMENT_GATE"
CONTEXT = "day-barrier-clear-context-v1"
LIVE_VERSION = "0.7.7"


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


def _parent_payload(sha: str) -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "status": "COMPLETE",
        "research_only": True,
        "label_free": True,
        "execution_authorized": False,
        "live_strategy_mutation": False,
        "parent_strategy_version": "0.7.5",
        "source_commit_sha": sha,
        "prospective_start_at": now,
        "captured_at": now,
        "admitted_this_run": 0,
        "inserted_this_run": 0,
        "total_frozen_parents": 0,
        "outcome_visibility": LOCKED,
        "execution_mode": "SHARED_STANDALONE_RESEARCH_SIDECAR",
        "current_live_strategy_version": LIVE_VERSION,
        "live_worker_inline_recorder": False,
        "live_worker_mutation": False,
    }


def _observer_payload(sha: str) -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "status": "COMPLETE",
        "observer_version": "day-barrier-clear-observer-v1",
        "context_version": CONTEXT,
        "research_only": True,
        "label_free": True,
        "execution_authorized": False,
        "live_strategy_mutation": False,
        "score_mutation": False,
        "ranking_mutation": False,
        "eligibility_mutation": False,
        "execution_mutation": False,
        "parent_strategy_version": "0.7.5",
        "source_commit_sha": sha,
        "captured_at": now,
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
        "current_live_strategy_version": LIVE_VERSION,
        "live_worker_inline_recorder": False,
        "live_worker_mutation": False,
    }


def _live_status(*, strategy_version: str | None = LIVE_VERSION) -> dict[str, object]:
    payload: dict[str, object] = {
        "prospective_funnel": {
            "status": "EXTERNALIZED",
            "enabled": False,
            "reason": "STANDALONE_RECORDER_OWNS_CAPTURE",
            "execution_mode": "STANDALONE_RAILWAY_CRON",
            "live_strategy_version": LIVE_VERSION,
        }
    }
    if strategy_version is not None:
        payload["strategy_version"] = strategy_version
    return payload


def _evaluate(sha: str, *, version_sha: str | None = None, live=None, funnel_sha=None, parent_sha=None, observer_sha=None):
    return preflight.evaluate_preflight(
        version={"commit_sha": version_sha or sha},
        live_status=live or _live_status(),
        prospective_status=_prospective_payload(funnel_sha or sha),
        barrier_parent_status=_parent_payload(parent_sha or sha),
        barrier_observer_status=_observer_payload(observer_sha or sha),
        expected_sha=sha,
    )


def test_exact_main_fresh_zero_sample_capture_passes_preflight():
    sha = "a" * 40
    result = _evaluate(sha)
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["live_strategy_version"] == LIVE_VERSION


def test_preflight_tracks_current_live_version_without_rewriting_frozen_parent_version():
    sha = "a" * 40
    result = _evaluate(sha, live=_live_status(strategy_version=LIVE_VERSION))
    assert result["ok"] is True
    assert _parent_payload(sha)["parent_strategy_version"] == "0.7.5"
    assert _observer_payload(sha)["parent_strategy_version"] == "0.7.5"


def test_missing_live_strategy_version_fails_preflight_closed():
    sha = "a" * 40
    result = _evaluate(sha, live=_live_status(strategy_version=None))
    assert result["ok"] is False
    assert "live strategy_version missing" in result["errors"]
    assert "expected live strategy_version missing" in result["errors"]


def test_live_marker_version_drift_fails_preflight_closed():
    sha = "a" * 40
    live = _live_status()
    live["prospective_funnel"]["live_strategy_version"] = "0.7.6"
    result = _evaluate(sha, live=live)
    assert result["ok"] is False
    assert "live strategy lineage mismatch" in result["errors"]


def test_parent_or_observer_live_version_drift_fails_preflight():
    sha = "a" * 40
    parent = _parent_payload(sha)
    observer = _observer_payload(sha)
    parent["current_live_strategy_version"] = "0.7.6"
    observer["current_live_strategy_version"] = "0.7.6"
    result = preflight.evaluate_preflight(
        version={"commit_sha": sha},
        live_status=_live_status(strategy_version=LIVE_VERSION),
        prospective_status=_prospective_payload(sha),
        barrier_parent_status=parent,
        barrier_observer_status=observer,
        expected_sha=sha,
    )
    assert result["ok"] is False
    assert "parent.current_live_strategy_version mismatch" in result["errors"]
    assert "observer.current_live_strategy_version mismatch" in result["errors"]


def test_any_stale_source_sha_fails_preflight_even_when_api_is_exact():
    expected = "a" * 40
    assert "standalone source SHA mismatch" in _evaluate(expected, funnel_sha="b" * 40)["errors"]
    assert "parent source SHA mismatch" in _evaluate(expected, parent_sha="b" * 40)["errors"]
    assert "observer source SHA mismatch" in _evaluate(expected, observer_sha="b" * 40)["errors"]


def test_non_externalized_live_worker_fails_preflight():
    sha = "a" * 40
    live = _live_status()
    live["prospective_funnel"] = {
        "status": "INLINE",
        "enabled": True,
        "reason": "WRONG_OWNER",
        "execution_mode": "INLINE",
    }
    result = _evaluate(sha, live=live)
    assert result["ok"] is False
    assert "prospective_funnel.status mismatch" in result["errors"]
    assert "prospective_funnel.enabled mismatch" in result["errors"]


def test_api_sha_mismatch_fails_preflight():
    expected = "a" * 40
    result = _evaluate(expected, version_sha="b" * 40)
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
        if path == "/v1/day-trade/research/barrier-clear-rearm/parent-status":
            return _parent_payload(expected)
        if path == "/v1/day-trade/research/barrier-clear-rearm/observer-status":
            return _observer_payload(expected)
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
