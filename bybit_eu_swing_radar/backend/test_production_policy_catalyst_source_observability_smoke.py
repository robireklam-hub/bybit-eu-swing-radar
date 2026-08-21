import os
from pathlib import Path
import subprocess
import sys

from scripts.production_policy_catalyst_source_observability_smoke import run_smoke, validate_status


SHA = "abc123"


def _row(code, *, enabled=True, available=True, fresh=True, event_count=1):
    if not enabled:
        return {
            "provider_code": code,
            "enabled": False,
            "collection_status": "NOT_CONFIGURED",
            "collection_freshness": "UNAVAILABLE",
            "event_store_status": "NOT_CONFIGURED",
            "event_store_spec_version": "policy-catalyst-event-store-v1",
            "event_store_event_count": 0,
            "context_only": True,
            "hard_gate": False,
            "score_mutation": False,
            "ranking_mutation": False,
            "eligibility_mutation": False,
            "execution_mutation": False,
        }
    return {
        "provider_code": code,
        "enabled": True,
        "collection_status": "AVAILABLE" if available else "UNAVAILABLE",
        "collection_freshness": "FRESH" if fresh else "STALE",
        "event_store_status": (
            "PERSISTED_EVENT_OBSERVED"
            if event_count > 0
            else "PENDING_NO_TIMESTAMPED_EVENT"
            if available
            else "UNAVAILABLE_SOURCE_COLLECTION"
        ),
        "event_store_spec_version": "policy-catalyst-event-store-v1",
        "event_store_event_count": event_count,
        "context_only": True,
        "hard_gate": False,
        "score_mutation": False,
        "ranking_mutation": False,
        "eligibility_mutation": False,
        "execution_mutation": False,
    }


def _status():
    rows = [
        _row("SEC"),
        _row("FED", event_count=0),
        _row("TREASURY"),
        _row("CFTC"),
        _row("WHITE_HOUSE"),
        _row("CONGRESS", enabled=False),
    ]
    return {
        "research_only": True,
        "context_only": True,
        "hard_gate": False,
        "live_strategy_mutated": False,
        "freshness": "FRESH",
        "latest_capture": {"source_commit_sha": SHA},
        "source_observability_v1": {
            "spec_version": "policy-catalyst-source-observability-v1",
            "event_store_spec_version": "policy-catalyst-event-store-v1",
            "enabled_source_count": 5,
            "available_source_count": 5,
            "fresh_source_count": 5,
            "persisted_event_source_count": 4,
            "sources": rows,
            "research_only": True,
            "context_only": True,
            "hard_gate": False,
            "live_strategy_mutated": False,
        },
    }


def test_validate_status_accepts_complete_source_observability_contract():
    ok, reason, summary = validate_status(_status(), SHA)
    assert ok is True
    assert reason == "ok"
    assert summary["enabled_source_count"] == 5
    assert summary["available_source_count"] == 5
    assert summary["fresh_source_count"] == 5
    assert summary["persisted_event_source_count"] == 4
    assert summary["pending_no_timestamped_event_sources"] == ["FED"]
    assert summary["provider_codes"] == ["CFTC", "CONGRESS", "FED", "SEC", "TREASURY", "WHITE_HOUSE"]


def test_validate_status_fails_when_expected_provider_row_is_missing():
    status = _status()
    status["source_observability_v1"]["sources"] = status["source_observability_v1"]["sources"][:-1]
    ok, reason, _ = validate_status(status, SHA)
    assert ok is False
    assert reason == "source_observability_provider_set_mismatch"


def test_validate_status_fails_on_trading_firewall_mutation():
    status = _status()
    status["source_observability_v1"]["sources"][0]["eligibility_mutation"] = True
    ok, reason, _ = validate_status(status, SHA)
    assert ok is False
    assert reason == "source_row_eligibility_mutation_not_false"


def test_validate_status_fails_on_observability_count_drift():
    status = _status()
    status["source_observability_v1"]["available_source_count"] = 4
    ok, reason, _ = validate_status(status, SHA)
    assert ok is False
    assert reason == "source_observability_available_source_count_mismatch"


def test_validate_status_surfaces_but_does_not_fail_temporary_source_unavailability():
    status = _status()
    row = status["source_observability_v1"]["sources"][0]
    row.update(
        {
            "collection_status": "UNAVAILABLE",
            "collection_freshness": "FRESH",
            "event_store_status": "UNAVAILABLE_SOURCE_COLLECTION",
            "event_store_event_count": 0,
        }
    )
    status["source_observability_v1"].update(
        {"available_source_count": 4, "persisted_event_source_count": 3}
    )
    ok, reason, summary = validate_status(status, SHA)
    assert ok is True
    assert reason == "ok"
    assert summary["operationally_unavailable_sources"] == ["SEC"]


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


def test_standalone_script_bootstraps_backend_root_without_pythonpath(tmp_path):
    script = Path(__file__).resolve().parent / "scripts" / "production_policy_catalyst_source_observability_smoke.py"
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PYTHONPATH",
            "PRODUCTION_RADAR_API_BASE_URL",
            "PRODUCTION_RADAR_API_KEY",
            "EXPECTED_SHA",
        }
    }
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 1
    assert "required policy-catalyst source-observability smoke configuration is missing" in completed.stdout
    assert "ModuleNotFoundError" not in completed.stderr
