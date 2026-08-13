from pathlib import Path


WORKFLOW = Path("../../.github/workflows/production-flow-freshness-smoke.yml")


def test_workflow_uses_successful_main_backend_tests_sha():
    text = WORKFLOW.read_text()
    assert 'workflows: ["Backend tests"]' in text
    assert "workflow_run.conclusion == 'success'" in text
    assert "workflow_run.head_branch == 'main'" in text
    assert "EXPECTED_SHA: ${{ github.event.workflow_run.head_sha }}" in text


def test_workflow_is_bounded_and_token_free_from_railway():
    text = WORKFLOW.read_text()
    assert "timeout-minutes: 10" in text
    assert "cancel-in-progress: true" in text
    assert "RAILWAY_API_TOKEN" not in text and "backboard.railway" not in text


def test_worker_execution_verified_only_by_script_gate():
    workflow = WORKFLOW.read_text()
    script = Path("scripts/production_flow_freshness_smoke.py").read_text()
    assert "/version" in script and "source_commit_sha" in script
    assert "DEPLOYMENT VERIFIED, WORKER EXECUTION VERIFIED." in script
