from pathlib import Path


WORKFLOW = Path("../../.github/workflows/production-flow-freshness-smoke.yml")


def test_pr_has_no_railway_or_production_api_execution_path():
    text = WORKFLOW.read_text()
    assert "pull_request:" not in text
    assert "RAILWAY_API_TOKEN" not in text
    assert "backboard.railway" not in text
    assert "railway_deployment_gate.py" not in text


def test_smoke_requires_successful_production_main_deployment():
    text = WORKFLOW.read_text()
    assert "deployment_status:" in text
    assert "deployment_status.state == 'success'" in text
    assert "deployment.environment == 'production'" in text
    assert "deployment.ref == 'main'" in text
    assert "deployment_status.environment_url != ''" in text
    assert "deployment_status.environment_url == vars.PRODUCTION_RADAR_API_BASE_URL" in text
    assert "format('{0}/', github.event.deployment_status.environment_url)" in text
    assert "format('{0}/', vars.PRODUCTION_RADAR_API_BASE_URL)" in text
    assert "ref: ${{ github.event.deployment.sha }}" in text
    assert "EXPECTED_SHA: ${{ github.event.deployment.sha }}" in text
    assert "deployments: read" in text
    assert "cancel-in-progress: true" in text


def test_deployment_failure_has_no_smoke_execution():
    text = WORKFLOW.read_text()
    condition = text.split("production-smoke:", 1)[1].split("runs-on:", 1)[0]
    assert "state == 'success'" in condition
    assert "production_flow_freshness_smoke.py" not in condition


def test_other_service_or_missing_environment_url_cannot_pass_gate():
    text = WORKFLOW.read_text()
    condition = text.split("production-smoke:", 1)[1].split("runs-on:", 1)[0]
    assert "environment_url != ''" in condition
    assert "PRODUCTION_RADAR_API_BASE_URL" in condition
    assert "RAILWAY_API_TOKEN" not in text
    assert "backboard.railway" not in text


def test_worker_execution_is_not_claimed_by_workflow_or_script():
    workflow = WORKFLOW.read_text()
    script = Path("scripts/production_flow_freshness_smoke.py").read_text()
    assert "MIN_FLOW_STATUS_TIME" not in workflow
    assert "WORKER EXECUTION VERIFIED" not in script
    assert "DEPLOYMENT VERIFIED, WORKER EXECUTION NOT VERIFIED." in script


def test_deployment_sha_is_the_only_commit_evidence_environment_variable():
    workflow = WORKFLOW.read_text()
    script = Path("scripts/production_flow_freshness_smoke.py").read_text()
    assert "EXPECTED_SHA: ${{ github.event.deployment.sha }}" in workflow
    assert 'os.getenv("EXPECTED_SHA"' in script
    assert 'os.getenv("GITHUB_SHA"' not in script
