"""Idempotently provision/deploy the standalone prospective funnel Railway service.

Trusted main-only workflow utility. Secret values are never printed.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENDPOINT = "https://backboard.railway.com/graphql/v2"
SERVICE_NAME = "prospective-funnel-worker"
REPO = "robireklam-hub/bybit-eu-swing-radar"
ROOT_DIRECTORY = "/bybit_eu_swing_radar/backend"
START_COMMAND = "python prospective_funnel_worker.py"
CRON_SCHEDULE = "2-57/5 * * * *"
RAILWAY_REGION = "europe-west4-drams3a"
DEPLOYMENT_POLL_SECONDS = 5
DEPLOYMENT_MAX_WAIT_SECONDS = 900
GQL_RETRY_ATTEMPTS = 4
TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}
REQUIRED_VARIABLES = (
    "BYBIT_BASE_URL",
    "COINALYZE_API_KEY",
    "COINALYZE_BASE_URL",
    "DATABASE_URL",
)
STATE_FILE = Path(".prospective_funnel_deployment.json")


def _gql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Call Railway GraphQL with bounded retries for transient transport failures."""
    token = os.environ["RAILWAY_API_TOKEN"]
    request = Request(
        ENDPOINT,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "prospective-funnel-provisioner/1",
        },
    )
    for attempt in range(GQL_RETRY_ATTEMPTS):
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode())
            if payload.get("errors"):
                safe = [
                    {"message": item.get("message"), "path": item.get("path")}
                    for item in payload["errors"]
                ]
                raise RuntimeError("Railway GraphQL error: " + json.dumps(safe))
            return payload.get("data") or {}
        except HTTPError as exc:
            if exc.code not in TRANSIENT_HTTP_CODES or attempt + 1 >= GQL_RETRY_ATTEMPTS:
                raise
        except URLError:
            if attempt + 1 >= GQL_RETRY_ATTEMPTS:
                raise
        time.sleep(min(2 ** attempt, 8))
    raise RuntimeError("Railway GraphQL retry loop exhausted")


def _services(project_id: str) -> list[dict[str, str]]:
    query = """
    query project($id:String!){
      project(id:$id){services{edges{node{id name}}}}
    }
    """
    data = _gql(query, {"id": project_id})
    edges = (((data.get("project") or {}).get("services") or {}).get("edges") or [])
    return [
        {"id": str(edge["node"]["id"]), "name": str(edge["node"]["name"])}
        for edge in edges
    ]


def _ensure_service(project_id: str) -> tuple[str, bool]:
    for service in _services(project_id):
        if service["name"] == SERVICE_NAME:
            return service["id"], False
    mutation = """
    mutation create($input:ServiceCreateInput!){
      serviceCreate(input:$input){id name}
    }
    """
    created = _gql(
        mutation,
        {
            "input": {
                "projectId": project_id,
                "name": SERVICE_NAME,
                "source": {"repo": REPO},
            }
        },
    ).get("serviceCreate") or {}
    service_id = str(created.get("id") or "")
    if not service_id:
        raise RuntimeError("Railway serviceCreate returned no service id")
    return service_id, True


def _source_variables(project_id: str, environment_id: str, service_id: str) -> dict[str, str]:
    query = """
    query variables($projectId:String!,$environmentId:String!,$serviceId:String,$unrendered:Boolean){
      variables(projectId:$projectId,environmentId:$environmentId,serviceId:$serviceId,unrendered:$unrendered)
    }
    """
    values = _gql(
        query,
        {
            "projectId": project_id,
            "environmentId": environment_id,
            "serviceId": service_id,
            "unrendered": True,
        },
    ).get("variables") or {}
    missing = [name for name in REQUIRED_VARIABLES if not values.get(name)]
    if missing:
        raise RuntimeError("Required source variables missing: " + ",".join(missing))
    return {name: str(values[name]) for name in REQUIRED_VARIABLES}


def _upsert_variables(
    project_id: str,
    environment_id: str,
    service_id: str,
    variables: dict[str, str],
) -> None:
    mutation = """
    mutation vars($input:VariableCollectionUpsertInput!){
      variableCollectionUpsert(input:$input)
    }
    """
    _gql(
        mutation,
        {
            "input": {
                "projectId": project_id,
                "environmentId": environment_id,
                "serviceId": service_id,
                "variables": variables,
            }
        },
    )


def _configure_service(environment_id: str, service_id: str) -> None:
    mutation = """
    mutation update($serviceId:String!,$environmentId:String!,$input:ServiceInstanceUpdateInput!){
      serviceInstanceUpdate(serviceId:$serviceId,environmentId:$environmentId,input:$input)
    }
    """
    _gql(
        mutation,
        {
            "serviceId": service_id,
            "environmentId": environment_id,
            "input": {
                "builder": "RAILPACK",
                "rootDirectory": ROOT_DIRECTORY,
                "startCommand": START_COMMAND,
                "cronSchedule": CRON_SCHEDULE,
                "restartPolicyType": "NEVER",
                "restartPolicyMaxRetries": 0,
                "multiRegionConfig": {
                    RAILWAY_REGION: {"numReplicas": 1},
                },
            },
        },
    )


def _deploy(environment_id: str, service_id: str, commit_sha: str) -> str:
    mutation = """
    mutation deploy($serviceId:String!,$environmentId:String!,$commitSha:String!){
      serviceInstanceDeployV2(serviceId:$serviceId,environmentId:$environmentId,commitSha:$commitSha)
    }
    """
    deployment_id = str(
        _gql(
            mutation,
            {
                "serviceId": service_id,
                "environmentId": environment_id,
                "commitSha": commit_sha,
            },
        ).get("serviceInstanceDeployV2")
        or ""
    )
    if not deployment_id:
        raise RuntimeError("Railway deploy returned no deployment id")
    return deployment_id


def _deployment_status(deployment_id: str) -> str:
    query = "query deployment($id:String!){deployment(id:$id){id status}}"
    deployment = _gql(query, {"id": deployment_id}).get("deployment") or {}
    return str(deployment.get("status") or "UNKNOWN")


def _recent_deployments(project_id: str, service_id: str) -> list[dict[str, Any]]:
    """List recent service deployments using Railway's documented public API query."""
    query = """
    query deployments($input:DeploymentListInput!){
      deployments(input:$input,first:20){edges{node{id status createdAt}}}
    }
    """
    data = _gql(query, {"input": {"projectId": project_id, "serviceId": service_id}})
    edges = ((data.get("deployments") or {}).get("edges") or [])
    return [dict(edge.get("node") or {}) for edge in edges]


def _parse_created_at(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _successful_same_run_deployment(
    deployments: list[dict[str, Any]],
    *,
    requested_id: str,
    not_before: datetime,
) -> str | None:
    """Return a sibling SUCCESS created by this provisioning run, if any.

    Railway may create a sibling deployment when service variables/configuration
    are updated immediately before the explicit exact-commit deploy. We only use
    a sibling as a transport-level provisioning success if it was created during
    this run. The following production smoke remains the exact-SHA semantic gate.
    """
    cutoff = not_before.astimezone(timezone.utc) - timedelta(seconds=5)
    for item in deployments:
        deployment_id = str(item.get("id") or "")
        if not deployment_id or deployment_id == requested_id:
            continue
        if str(item.get("status") or "").upper() != "SUCCESS":
            continue
        created_at = _parse_created_at(item.get("createdAt"))
        if created_at is not None and created_at >= cutoff:
            return deployment_id
    return None


def main() -> int:
    project_id = os.environ["RAILWAY_PROJECT_ID"]
    environment_id = os.environ["RAILWAY_ENVIRONMENT_ID"]
    day_service_id = os.environ["DAY_RADAR_SERVICE_ID"]
    commit_sha = os.environ["EXPECTED_API_SHA"]

    service_id, created = _ensure_service(project_id)
    source_vars = _source_variables(project_id, environment_id, day_service_id)
    provision_started_at = datetime.now(timezone.utc)
    _upsert_variables(project_id, environment_id, service_id, source_vars)
    _configure_service(environment_id, service_id)
    requested_deployment_id = _deploy(environment_id, service_id, commit_sha)
    effective_deployment_id = requested_deployment_id

    print("PROSPECTIVE_SERVICE_ID=" + service_id, flush=True)
    print("PROSPECTIVE_SERVICE_CREATED=" + str(created).lower(), flush=True)
    print("PROSPECTIVE_DEPLOYMENT_ID=" + requested_deployment_id, flush=True)
    print("PROSPECTIVE_REGION=" + RAILWAY_REGION, flush=True)
    print("VARIABLE_NAMES_COPIED=" + json.dumps(sorted(source_vars)), flush=True)

    final_status = "UNKNOWN"
    max_attempts = max(1, DEPLOYMENT_MAX_WAIT_SECONDS // DEPLOYMENT_POLL_SECONDS)
    for attempt in range(max_attempts):
        final_status = _deployment_status(requested_deployment_id)
        if attempt % 6 == 0:
            print("DEPLOYMENT_STATUS=" + final_status, flush=True)
        if final_status == "SUCCESS":
            break

        sibling_id = None
        if attempt % 6 == 0 or final_status in {"FAILED", "CRASHED", "REMOVED", "CANCELLED"}:
            sibling_id = _successful_same_run_deployment(
                _recent_deployments(project_id, service_id),
                requested_id=requested_deployment_id,
                not_before=provision_started_at,
            )
        if sibling_id:
            effective_deployment_id = sibling_id
            final_status = "SUCCESS"
            print("PROSPECTIVE_EFFECTIVE_DEPLOYMENT_ID=" + sibling_id, flush=True)
            print("DEPLOYMENT_STATUS=SUCCESS_SIBLING", flush=True)
            break

        if final_status in {"FAILED", "CRASHED", "REMOVED", "CANCELLED"}:
            raise RuntimeError("Standalone prospective deployment ended: " + final_status)
        time.sleep(DEPLOYMENT_POLL_SECONDS)
    if final_status != "SUCCESS":
        raise RuntimeError(
            "Standalone prospective deployment did not reach SUCCESS within "
            + str(DEPLOYMENT_MAX_WAIT_SECONDS)
            + "s; final_status="
            + final_status
        )

    STATE_FILE.write_text(
        json.dumps(
            {
                "service_id": service_id,
                "deployment_id": effective_deployment_id,
                "requested_deployment_id": requested_deployment_id,
                "commit_sha": commit_sha,
                "region": RAILWAY_REGION,
                "created": created,
            },
            sort_keys=True,
        )
    )
    print("PROSPECTIVE_FUNNEL_SERVICE_DEPLOYED.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
