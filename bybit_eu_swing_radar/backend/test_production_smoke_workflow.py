from pathlib import Path


WORKFLOW = Path("../../.github/workflows/production-flow-freshness-smoke.yml")


def _job_gate(*, conclusion: str, event: str, head_branch: str, head_sha: str) -> bool:
    """Mirror the declarative Actions job condition for deterministic cases."""
    return (
        conclusion == "success"
        and event == "push"
        and head_branch == "main"
        and head_sha != ""
    )


def test_workflow_uses_successful_main_push_backend_tests_sha():
    text = WORKFLOW.read_text()
    assert 'workflows: ["Backend tests"]' in text
    assert "types: [completed]" in text
    assert "workflow_run.conclusion == 'success'" in text
    assert "workflow_run.event == 'push'" in text
    assert "workflow_run.head_branch == 'main'" in text
    assert "workflow_run.head_sha != ''" in text
    assert "EXPECTED_SHA: ${{ github.event.workflow_run.head_sha }}" in text
    assert "ref: ${{ github.event.workflow_run.head_sha }}" in text


def test_workflow_gate_accepts_only_successful_main_push_with_sha():
    assert _job_gate(
        conclusion="success", event="push", head_branch="main", head_sha="abc123"
    )
    assert not _job_gate(
        conclusion="success", event="pull_request", head_branch="main", head_sha="abc123"
    )
    for conclusion in ("failure", "cancelled", "skipped"):
        assert not _job_gate(
            conclusion=conclusion, event="push", head_branch="main", head_sha="abc123"
        )
    assert not _job_gate(
        conclusion="success", event="push", head_branch="feature", head_sha="abc123"
    )
    assert not _job_gate(
        conclusion="success", event="push", head_branch="main", head_sha=""
    )


def test_workflow_is_bounded_and_token_free_from_railway():
    text = WORKFLOW.read_text()
    assert "timeout-minutes: 10" in text
    assert "group: production-flow-freshness-smoke-production" in text
    assert "cancel-in-progress: true" in text
    assert "RAILWAY_API_TOKEN" not in text and "backboard.railway" not in text
    assert "deployment_status" not in text
    assert "environment_url" not in text


def test_workflow_only_declares_workflow_run_trigger():
    text = WORKFLOW.read_text()
    assert "on:\n  workflow_run:" in text
    assert "pull_request:" not in text
    assert "push:" not in text
    assert "workflow_dispatch:" not in text


def test_worker_execution_verified_only_by_script_gate():
    workflow = WORKFLOW.read_text()
    script = Path("scripts/production_flow_freshness_smoke.py").read_text()
    assert "/version" in script and "source_commit_sha" in script
    assert "DEPLOYMENT VERIFIED, WORKER EXECUTION VERIFIED." in script
