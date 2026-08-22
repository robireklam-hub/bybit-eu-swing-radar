import os
from pathlib import Path
import subprocess
import sys

from scripts.production_day_barrier_clear_v2_status import validate_v2_status


def _payload(**overrides):
    v2 = {
        "status_spec_version": "day-barrier-clear-rearm-v2-status-v1",
        "trial_id": "day-barrier-clear-rearm-v2",
        "activated": True,
        "activation_boundary": "2026-08-22T13:15:00+00:00",
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "historical_backfill_allowed": False,
        "v1_event_reuse_allowed": False,
        "pre_activation_parent_reuse_allowed": False,
        "development_target": 60,
        "development_per_side": 30,
        "validation_target": 40,
        "validation_per_side": 20,
        "outcome_visible": False,
        "threshold_search_allowed": False,
        "promotion_allowed": False,
        "execution_authorized": False,
        "live_strategy_mutated": False,
        "source_commit_sha": "a" * 40,
        "eligible_terminal_event_count": 3,
        "eligible_long_count": 2,
        "eligible_short_count": 1,
        "excluded_pre_activation_parent_count": 5,
        "development_ready": False,
        "development_event_count": 0,
        "development_long_count": 0,
        "development_short_count": 0,
        "development_fingerprint": None,
        "validation_ready": False,
        "validation_event_count": 0,
        "validation_long_count": 0,
        "validation_short_count": 0,
        "validation_fingerprint": None,
    }
    v2.update(overrides)
    return {"v2": v2}


def test_v2_production_validator_accepts_closed_partial_cohort_without_opening_outcomes():
    evidence = validate_v2_status(_payload(), expected_sha="a" * 40)
    assert evidence["ok"] is True
    assert evidence["development_ready"] is False
    assert evidence["outcome_visible"] is False
    assert evidence["promotion_allowed"] is False


def test_v2_production_validator_rejects_partial_development_freeze():
    evidence = validate_v2_status(
        _payload(development_event_count=12, development_fingerprint="bad"),
        expected_sha="a" * 40,
    )
    assert evidence["ok"] is False
    assert "partial DEVELOPMENT freeze detected" in evidence["errors"]


def test_v2_production_validator_rejects_source_sha_drift_and_parent_reuse_drift():
    evidence = validate_v2_status(
        _payload(source_commit_sha="b" * 40, pre_activation_parent_reuse_allowed=True),
        expected_sha="a" * 40,
    )
    assert evidence["ok"] is False
    assert "v2.source_commit_sha mismatch" in evidence["errors"]
    assert "v2.pre_activation_parent_reuse_allowed mismatch" in evidence["errors"]


def test_v2_production_script_imports_standalone_from_foreign_working_directory(tmp_path):
    script = Path(__file__).resolve().parent / "scripts" / "production_day_barrier_clear_v2_status.py"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy, sys; runpy.run_path(sys.argv[1], run_name='barrier_v2_import_probe')",
            str(script),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr
