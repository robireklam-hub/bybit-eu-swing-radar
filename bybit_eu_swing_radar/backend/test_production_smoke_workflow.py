from pathlib import Path


WORKFLOW = Path("../../.github/workflows/production-flow-freshness-smoke.yml")


def test_pr_has_no_railway_or_production_api_execution_path():
    text = WORKFLOW.read_text()
    assert "pull_request:" not in text
    assert "RAILWAY_API_TOKEN" not in text
    assert "backboard.railway" not in text
    assert "railway_deployment_gate.py" not in text


def workflow_run_gate(*, conclusion="success", event="push", branch="main", sha="abc"):
    return conclusion == "success" and event == "push" and branch == "main" and sha != ""


def test_smoke_starts_when_backend_tests_completes():
    text = WORKFLOW.read_text()
    assert 'workflows: ["Backend tests"]' in text
    assert "types: [completed]" in text
    assert "cancel-in-progress: true" in text
    assert "timeout-minutes: 10" in text


def test_workflow_run_gate_allows_only_successful_main_push_with_sha():
    text = WORKFLOW.read_text()
    condition = text.split("production-smoke:", 1)[1].split("runs-on:", 1)[0]
    assert "workflow_run.conclusion == 'success'" in condition
    assert "workflow_run.event == 'push'" in condition
    assert "workflow_run.head_branch == 'main'" in condition
    assert "workflow_run.head_sha != ''" in condition
    assert workflow_run_gate()
    assert not workflow_run_gate(event="pull_request")
    assert not workflow_run_gate(branch="feature")
    assert not workflow_run_gate(sha="")


def test_unsuccessful_workflow_run_cannot_pass_gate():
    for conclusion in ("failure", "cancelled", "skipped"):
        assert not workflow_run_gate(conclusion=conclusion)


def test_deployment_and_railway_execution_paths_are_absent():
    text = WORKFLOW.read_text()
    assert "deployment_status" not in text
    assert "environment_url" not in text
    assert "github.event.deployment." not in text
    assert "RAILWAY_API_TOKEN" not in text
    assert "backboard.railway" not in text


def test_worker_execution_is_not_claimed_by_workflow_or_script():
    workflow = WORKFLOW.read_text()
    script = Path("scripts/production_flow_freshness_smoke.py").read_text()
    assert "MIN_FLOW_STATUS_TIME" not in workflow
    assert "WORKER EXECUTION VERIFIED" not in script
    assert "DEPLOYMENT VERIFIED, WORKER EXECUTION NOT VERIFIED." in script


def test_workflow_run_sha_is_used_for_checkout_and_commit_evidence():
    workflow = WORKFLOW.read_text()
    script = Path("scripts/production_flow_freshness_smoke.py").read_text()
    assert "EXPECTED_SHA: ${{ github.event.workflow_run.head_sha }}" in workflow
    assert "ref: ${{ github.event.workflow_run.head_sha }}" in workflow
    assert 'os.getenv("EXPECTED_SHA"' in script
    assert 'os.getenv("GITHUB_SHA"' not in script
