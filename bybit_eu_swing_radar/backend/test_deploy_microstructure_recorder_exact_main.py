from __future__ import annotations

import pytest

from scripts import deploy_microstructure_recorder_exact_main as deployer


def test_request_exact_deployment_passes_commit_sha(monkeypatch):
    seen = {}

    def gql(query, variables):
        seen["query"] = query
        seen["variables"] = variables
        return {"serviceInstanceDeployV2": "deployment-123"}

    monkeypatch.setattr(deployer, "_gql", gql)
    sha = "a" * 40
    deployment_id = deployer.request_exact_deployment("env", "service", sha)

    assert deployment_id == "deployment-123"
    assert "serviceInstanceDeployV2" in seen["query"]
    assert seen["variables"] == {
        "serviceId": "service",
        "environmentId": "env",
        "commitSha": sha,
    }


def test_request_exact_deployment_rejects_non_full_sha(monkeypatch):
    monkeypatch.setattr(deployer, "_gql", lambda *_: pytest.fail("GraphQL must not run"))
    with pytest.raises(ValueError, match="40-character"):
        deployer.request_exact_deployment("env", "service", "abc123")


def test_request_exact_deployment_requires_deployment_id(monkeypatch):
    monkeypatch.setattr(deployer, "_gql", lambda *_: {"serviceInstanceDeployV2": None})
    with pytest.raises(RuntimeError, match="no deployment id"):
        deployer.request_exact_deployment("env", "service", "b" * 40)


def test_wait_for_success_polls_requested_deployment_only(monkeypatch):
    statuses = iter(["BUILDING", "DEPLOYING", "SUCCESS"])
    sleeps = []
    monkeypatch.setattr(deployer, "deployment_status", lambda _deployment_id: next(statuses))
    monkeypatch.setattr(deployer.time, "sleep", sleeps.append)

    assert deployer.wait_for_success("deployment-123") == "SUCCESS"
    assert sleeps == [deployer.DEPLOYMENT_POLL_SECONDS, deployer.DEPLOYMENT_POLL_SECONDS]


def test_wait_for_success_fails_on_terminal_state(monkeypatch):
    monkeypatch.setattr(deployer, "deployment_status", lambda _deployment_id: "CRASHED")
    monkeypatch.setattr(deployer.time, "sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError, match="CRASHED"):
        deployer.wait_for_success("deployment-123")


def test_deployer_contains_no_variable_or_owner_mutation():
    source = open("scripts/deploy_microstructure_recorder_exact_main.py", encoding="utf-8").read()
    assert "variableCollectionUpsert" not in source
    assert "serviceInstanceUpdate" not in source
    assert "MICROSTRUCTURE_RECORDER_OWNER" not in source
    assert "MICROSTRUCTURE_RECORDER_ENABLED" not in source
    assert "serviceInstanceDeployV2" in source
