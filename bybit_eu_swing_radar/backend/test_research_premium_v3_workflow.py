from pathlib import Path


WORKFLOW = Path("../../.github/workflows/v073-research-premium-v3-auto.yml")
SCRIPT = Path("scripts/run_production_research_premium_v3.py")


def test_premium_v3_workflow_is_main_production_smoke_gated():
    text = WORKFLOW.read_text()
    assert 'workflows: ["Production Flow freshness smoke"]' in text
    assert "types: [completed]" in text
    assert "pull_request:" not in text
    assert "workflow_run.conclusion == 'success'" in text
    assert "workflow_run.head_branch == 'main'" in text
    assert "workflow_run.head_sha != ''" in text


def test_premium_v3_workflow_pins_verified_sha_and_existing_secret_contract():
    text = WORKFLOW.read_text()
    assert "EXPECTED_SHA: ${{ github.event.workflow_run.head_sha }}" in text
    assert "ref: ${{ github.event.workflow_run.head_sha }}" in text
    assert "PRODUCTION_RADAR_API_BASE_URL: ${{ vars.PRODUCTION_RADAR_API_BASE_URL }}" in text
    assert "PRODUCTION_RADAR_API_KEY: ${{ secrets.PRODUCTION_RADAR_API_KEY }}" in text
    assert 'test "$(git rev-parse HEAD)" = "${EXPECTED_SHA}"' in text


def test_premium_v3_dispatcher_requires_domain_report_and_surfaces_holdout_metrics():
    script = SCRIPT.read_text()
    assert "/v1/day-trade/research/premium/v3/run" in script
    assert "/v1/day-trade/research/premium/v3/status" in script
    assert "/v1/day-trade/research/premium/v3/report" in script
    assert 'if state == "FAILED"' in script
    assert "internal_holdout_edge_pass" in script
    assert "promotion_allowed" in script
