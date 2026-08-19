from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scripts import provision_prospective_funnel_worker as provisioner


def test_configure_service_pins_eu_west(monkeypatch):
    seen: dict[str, object] = {}

    def fake_gql(query: str, variables: dict[str, object]):
        seen["query"] = query
        seen["variables"] = variables
        return {"serviceInstanceUpdate": True}

    monkeypatch.setattr(provisioner, "_gql", fake_gql)

    provisioner._configure_service("environment-1", "service-1")

    assert "serviceInstanceUpdate" in str(seen["query"])
    assert seen["variables"] == {
        "serviceId": "service-1",
        "environmentId": "environment-1",
        "input": {
            "builder": "RAILPACK",
            "rootDirectory": provisioner.ROOT_DIRECTORY,
            "startCommand": provisioner.START_COMMAND,
            "cronSchedule": provisioner.CRON_SCHEDULE,
            "restartPolicyType": "NEVER",
            "restartPolicyMaxRetries": 0,
            "multiRegionConfig": {
                "europe-west4-drams3a": {"numReplicas": 1},
            },
        },
    }


def test_deploy_uses_scalar_service_instance_deploy_v2(monkeypatch):
    seen: dict[str, object] = {}

    def fake_gql(query: str, variables: dict[str, str]):
        seen["query"] = query
        seen["variables"] = variables
        return {"serviceInstanceDeployV2": "deployment-123"}

    monkeypatch.setattr(provisioner, "_gql", fake_gql)

    deployment_id = provisioner._deploy("environment-1", "service-1", "abc123")

    assert deployment_id == "deployment-123"
    assert "serviceInstanceDeployV2(serviceId:$serviceId,environmentId:$environmentId,commitSha:$commitSha)" in str(
        seen["query"]
    )
    assert "{id status}" not in str(seen["query"])
    assert seen["variables"] == {
        "serviceId": "service-1",
        "environmentId": "environment-1",
        "commitSha": "abc123",
    }


def test_deploy_rejects_missing_deployment_id(monkeypatch):
    monkeypatch.setattr(
        provisioner,
        "_gql",
        lambda _query, _variables: {"serviceInstanceDeployV2": ""},
    )

    with pytest.raises(RuntimeError, match="no deployment id"):
        provisioner._deploy("environment-1", "service-1", "abc123")


def test_gql_retries_direct_read_timeouts(monkeypatch):
    calls = 0

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"data":{"ok":true}}'

    def fake_urlopen(_request, timeout: int):
        nonlocal calls
        calls += 1
        assert timeout == 30
        if calls < provisioner.GQL_RETRY_ATTEMPTS:
            raise TimeoutError("read operation timed out")
        return FakeResponse()

    monkeypatch.setenv("RAILWAY_API_TOKEN", "test-token")
    monkeypatch.setattr(provisioner, "urlopen", fake_urlopen)
    monkeypatch.setattr(provisioner.time, "sleep", lambda _seconds: None)

    assert provisioner._gql("query { ok }", {}) == {"ok": True}
    assert calls == provisioner.GQL_RETRY_ATTEMPTS


def test_recent_deployments_uses_documented_service_query(monkeypatch):
    seen: dict[str, object] = {}

    def fake_gql(query: str, variables: dict[str, object]):
        seen["query"] = query
        seen["variables"] = variables
        return {
            "deployments": {
                "edges": [
                    {
                        "node": {
                            "id": "deployment-2",
                            "status": "SUCCESS",
                            "createdAt": "2026-08-19T00:53:19Z",
                        }
                    }
                ]
            }
        }

    monkeypatch.setattr(provisioner, "_gql", fake_gql)

    rows = provisioner._recent_deployments("project-1", "service-1")

    assert rows == [
        {
            "id": "deployment-2",
            "status": "SUCCESS",
            "createdAt": "2026-08-19T00:53:19Z",
        }
    ]
    assert "deployments(input:$input,first:20)" in str(seen["query"])
    assert seen["variables"] == {
        "input": {"projectId": "project-1", "serviceId": "service-1"}
    }


def test_same_run_successful_sibling_can_replace_stuck_requested_deployment():
    started = datetime(2026, 8, 19, 0, 53, 15, tzinfo=timezone.utc)
    deployments = [
        {
            "id": "requested",
            "status": "QUEUED",
            "createdAt": "2026-08-19T00:53:18Z",
        },
        {
            "id": "sibling-success",
            "status": "SUCCESS",
            "createdAt": "2026-08-19T00:53:19Z",
        },
    ]

    assert provisioner._successful_same_run_deployment(
        deployments,
        requested_id="requested",
        not_before=started,
    ) == "sibling-success"


def test_old_successful_deployment_cannot_mask_current_failure():
    started = datetime(2026, 8, 19, 0, 53, 15, tzinfo=timezone.utc)
    deployments = [
        {
            "id": "old-success",
            "status": "SUCCESS",
            "createdAt": (started - timedelta(minutes=30)).isoformat(),
        },
        {
            "id": "requested",
            "status": "REMOVED",
            "createdAt": "2026-08-19T00:53:18Z",
        },
    ]

    assert provisioner._successful_same_run_deployment(
        deployments,
        requested_id="requested",
        not_before=started,
    ) is None


def test_requested_deployment_is_never_mistaken_for_sibling():
    started = datetime(2026, 8, 19, 0, 53, 15, tzinfo=timezone.utc)
    deployments = [
        {
            "id": "requested",
            "status": "SUCCESS",
            "createdAt": "2026-08-19T00:53:18Z",
        }
    ]

    assert provisioner._successful_same_run_deployment(
        deployments,
        requested_id="requested",
        not_before=started,
    ) is None
