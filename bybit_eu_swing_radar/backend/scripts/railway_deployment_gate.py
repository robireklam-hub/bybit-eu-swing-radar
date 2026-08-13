#!/usr/bin/env python3
"""Read-only Railway deployment gate for the production smoke workflow."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen


RAILWAY_GRAPHQL_URL = "https://backboard.railway.com/graphql/v2"
TERMINAL_FAILURES = {"FAILED", "CRASHED", "REMOVED", "SKIPPED"}
API_CALLS = 0


def parse_created_at(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("deployment createdAt is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("deployment createdAt has no timezone")
    return parsed


def latest_for_commit(deployments: list[dict[str, Any]], expected_sha: str):
    matching = []
    for deployment in deployments:
        meta = deployment.get("meta")
        if isinstance(meta, str):
            meta = json.loads(meta)
        if isinstance(meta, dict) and meta.get("commitHash") == expected_sha:
            matching.append(deployment)
    return max(matching, key=lambda item: parse_created_at(item.get("createdAt")), default=None)


def deployment_result(deployments: list[dict[str, Any]], expected_sha: str) -> str:
    latest = latest_for_commit(deployments, expected_sha)
    if latest is None:
        return "WAIT"
    status = str(latest.get("status", "")).upper()
    if status == "SUCCESS":
        return "PASS"
    if status in TERMINAL_FAILURES:
        return "FAIL"
    return "WAIT"


def railway_query(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    global API_CALLS
    API_CALLS += 1
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    request = Request(
        RAILWAY_GRAPHQL_URL,
        data=body,
        method="POST",
        headers={
            "Project-Access-Token": os.environ["RAILWAY_API_TOKEN"],
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.load(response)
    except HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("Railway API rate limit reached") from None
        raise
    if result.get("errors"):
        raise RuntimeError("Railway GraphQL query failed")
    return result["data"]


def verify_project_token_scope() -> None:
    data = railway_query("""
    query {
      projectToken { projectId environmentId }
    }
    """)
    scope = data.get("projectToken") or {}
    if scope.get("projectId") != os.environ["RAILWAY_PROJECT_ID"]:
        raise RuntimeError("Railway project token project scope mismatch")
    if scope.get("environmentId") != os.environ["RAILWAY_ENVIRONMENT_ID"]:
        raise RuntimeError("Railway project token environment scope mismatch")


def query_deployments(service_id: str) -> list[dict[str, Any]]:
    query = """
    query Deployments($input: DeploymentListInput!) {
      deployments(input: $input, first: 20) {
        edges { node { id status meta createdAt } }
      }
    }
    """
    data = railway_query(query, {"input": {
        "projectId": os.environ["RAILWAY_PROJECT_ID"],
        "environmentId": os.environ["RAILWAY_ENVIRONMENT_ID"],
        "serviceId": service_id,
    }})
    return [edge["node"] for edge in data["deployments"]["edges"]]


def query_both_deployments(api_service_id: str, worker_service_id: str):
    query = """
    query Deployments($api: DeploymentListInput!, $worker: DeploymentListInput!) {
      api: deployments(input: $api, first: 20) {
        edges { node { id status meta createdAt } }
      }
      worker: deployments(input: $worker, first: 20) {
        edges { node { id status meta createdAt } }
      }
    }
    """
    common = {
        "projectId": os.environ["RAILWAY_PROJECT_ID"],
        "environmentId": os.environ["RAILWAY_ENVIRONMENT_ID"],
    }
    data = railway_query(query, {
        "api": {**common, "serviceId": api_service_id},
        "worker": {**common, "serviceId": worker_service_id},
    })
    return {
        "api": [edge["node"] for edge in data["api"]["edges"]],
        "flow-worker": [edge["node"] for edge in data["worker"]["edges"]],
    }


def wait_for_deployments(
    expected_sha: str,
    services: dict[str, str],
    *,
    query: Callable[[str], list[dict[str, Any]]] = query_deployments,
    timeout_seconds: float = 900,
    poll_seconds: float = 15,
) -> int:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        waiting = False
        for name, service_id in services.items():
            result = deployment_result(query(service_id), expected_sha)
            if result == "FAIL":
                print(f"FAIL Railway {name} latest deployment failed for expected commit")
                return 1
            waiting |= result != "PASS"
        if not waiting:
            print("DEPLOYMENT VERIFIED, WORKER EXECUTION NOT VERIFIED.")
            return 0
        time.sleep(poll_seconds)
    print("FAIL timed out waiting for matching successful Railway deployments")
    return 1


def wait_for_both_deployments(expected_sha: str, *, timeout_seconds=990, poll_seconds=30) -> int:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        rows = query_both_deployments(
            os.environ["RAILWAY_API_SERVICE_ID"],
            os.environ["RAILWAY_FLOW_WORKER_SERVICE_ID"],
        )
        results = {name: deployment_result(items, expected_sha) for name, items in rows.items()}
        failed = next((name for name, result in results.items() if result == "FAIL"), None)
        if failed:
            print(f"FAIL Railway {failed} latest deployment failed for expected commit")
            return 1
        if all(result == "PASS" for result in results.values()):
            print("DEPLOYMENT VERIFIED, WORKER EXECUTION NOT VERIFIED.")
            return 0
        time.sleep(poll_seconds)
    print("FAIL timed out waiting for matching successful Railway deployments")
    return 1


def validate_schema_once(expected_sha: str) -> int:
    results = {}
    for name, service_id in {
        "api": os.environ["RAILWAY_API_SERVICE_ID"],
        "flow-worker": os.environ["RAILWAY_FLOW_WORKER_SERVICE_ID"],
    }.items():
        rows = query_deployments(service_id)
        if any(not isinstance(item.get("meta"), (dict, str)) for item in rows):
            raise RuntimeError("Railway deployment meta.commitHash is unavailable")
        if not any(
            isinstance(json.loads(item["meta"]) if isinstance(item["meta"], str) else item["meta"], dict)
            and (json.loads(item["meta"]) if isinstance(item["meta"], str) else item["meta"]).get("commitHash")
            for item in rows
        ):
            raise RuntimeError("Railway deployment meta.commitHash is unavailable")
        results[name] = deployment_result(rows, expected_sha)
    if not all(result == "PASS" for result in results.values()):
        print("FAIL deployed PR base commit was not verified for both Railway services")
        return 1
    print("PASS Railway deployment schema and deployed PR base commit verified")
    return 0


def main() -> int:
    required = (
        "RAILWAY_API_TOKEN", "RAILWAY_PROJECT_ID", "RAILWAY_ENVIRONMENT_ID",
        "RAILWAY_API_SERVICE_ID", "RAILWAY_FLOW_WORKER_SERVICE_ID", "EXPECTED_SHA",
    )
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        print(f"FAIL missing deployment-gate configuration: {', '.join(missing)}")
        return 1
    services = {
        "api": os.environ["RAILWAY_API_SERVICE_ID"],
        "flow-worker": os.environ["RAILWAY_FLOW_WORKER_SERVICE_ID"],
    }
    try:
        verify_project_token_scope()
        if os.getenv("RAILWAY_GATE_MODE") == "validate-once":
            return validate_schema_once(os.environ["EXPECTED_SHA"])
        return wait_for_both_deployments(os.environ["EXPECTED_SHA"])
    except Exception:
        print("FAIL Railway deployment query failed; credentials and response are redacted")
        return 1
    finally:
        print(f"Railway API calls: {API_CALLS}")


if __name__ == "__main__":
    sys.exit(main())
