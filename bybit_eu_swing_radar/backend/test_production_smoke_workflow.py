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
    assert "ref: ${{ github.event.deployment.sha }}" in text
    assert "deployments: read" in text


def test_deployment_failure_has_no_smoke_execution():
    text = WORKFLOW.read_text()
    condition = text.split("production-smoke:", 1)[1].split("runs-on:", 1)[0]
    assert "state == 'success'" in condition
    assert "production_flow_freshness_smoke.py" not in condition
