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


def test_build_boundary_repairs_only_fixed_recorder_build_fields(monkeypatch):
    states = iter(
        [
            {
                "id": "instance",
                "rootDirectory": None,
                "startCommand": deployer.RECORDER_START_COMMAND,
                "builder": "RAILPACK",
            },
            {
                "id": "instance",
                "rootDirectory": deployer.RECORDER_ROOT_DIRECTORY,
                "startCommand": deployer.RECORDER_START_COMMAND,
                "builder": "RAILPACK",
            },
        ]
    )
    calls = []

    monkeypatch.setattr(deployer, "recorder_service_instance", lambda *_: next(states))

    def gql(query, variables):
        calls.append((query, variables))
        return {"serviceInstanceUpdate": True}

    monkeypatch.setattr(deployer, "_gql", gql)
    deployer.ensure_recorder_build_boundary("env", "service")

    assert len(calls) == 1
    query, variables = calls[0]
    assert "serviceInstanceUpdate" in query
    assert variables == {
        "serviceId": "service",
        "environmentId": "env",
        "input": {
            "rootDirectory": "/bybit_eu_swing_radar/backend",
            "startCommand": "python -m research.microstructure.standalone",
            "builder": "RAILPACK",
        },
    }


def test_build_boundary_is_noop_when_already_correct(monkeypatch):
    state = {
        "id": "instance",
        "rootDirectory": deployer.RECORDER_ROOT_DIRECTORY,
        "startCommand": deployer.RECORDER_START_COMMAND,
        "builder": "RAILPACK",
    }
    monkeypatch.setattr(deployer, "recorder_service_instance", lambda *_: dict(state))
    monkeypatch.setattr(deployer, "_gql", lambda *_: pytest.fail("update must not run"))
    deployer.ensure_recorder_build_boundary("env", "service")


def test_build_boundary_fails_closed_if_update_does_not_stick(monkeypatch):
    state = {
        "id": "instance",
        "rootDirectory": None,
        "startCommand": deployer.RECORDER_START_COMMAND,
        "builder": "RAILPACK",
    }
    monkeypatch.setattr(deployer, "recorder_service_instance", lambda *_: dict(state))
    monkeypatch.setattr(deployer, "_gql", lambda *_: {"serviceInstanceUpdate": True})
    with pytest.raises(RuntimeError, match="build boundary mismatch"):
        deployer.ensure_recorder_build_boundary("env", "service")


def test_region_always_applies_eu_west_only(monkeypatch):
    calls = []

    def gql(query, variables):
        calls.append((query, variables))
        return {"serviceInstanceUpdate": True}

    monkeypatch.setattr(deployer, "_gql", gql)
    deployer.apply_recorder_region("env", "service")

    assert len(calls) == 1
    assert "serviceInstanceUpdate" in calls[0][0]
    assert calls[0][1] == {
        "serviceId": "service",
        "environmentId": "env",
        "input": {"multiRegionConfig": {"europe-west4-drams3a": {"numReplicas": 1}}},
    }


def test_region_fails_closed_if_mutation_not_confirmed(monkeypatch):
    monkeypatch.setattr(deployer, "_gql", lambda *_: {"serviceInstanceUpdate": False})
    with pytest.raises(RuntimeError, match="update was not confirmed"):
        deployer.apply_recorder_region("env", "service")


def test_deployer_contains_no_variable_or_owner_mutation():
    source = open("scripts/deploy_microstructure_recorder_exact_main.py", encoding="utf-8").read()
    assert "variableCollectionUpsert" not in source
    assert "MICROSTRUCTURE_RECORDER_OWNER" not in source
    assert "MICROSTRUCTURE_RECORDER_ENABLED" not in source
    assert "serviceInstanceDeployV2" in source
    assert "serviceInstanceUpdate" in source
    assert "europe-west4-drams3a" in source
