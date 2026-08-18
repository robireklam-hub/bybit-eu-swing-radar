"""Read-only Railway scheduler diagnostics for the production day-radar-worker.

This script is intended only for the trusted main-only production smoke workflow.
It prints safe scheduler/deployment metadata and the current public day-worker
provenance. It never mutates Railway or production state and never prints tokens
or environment variables.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

RAILWAY_ENDPOINT = "https://backboard.railway.com/graphql/v2"
ACTIVE_DEPLOYMENT_STATES = {
    "BUILDING",
    "DEPLOYING",
    "INITIALIZING",
    "QUEUED",
    "WAITING",
}


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def summarize_scheduler(
    *,
    cron_schedule: str | None,
    deployments: list[dict[str, Any]],
    checked_at: str | None,
    worker_sha: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a deterministic, non-mutating scheduler diagnosis summary."""
    now = now or datetime.now(timezone.utc)
    checked = _parse_dt(checked_at)
    age = None if checked is None else max(0.0, (now - checked).total_seconds())
    active = [
        item
        for item in deployments
        if str(item.get("status") or "").upper() in ACTIVE_DEPLOYMENT_STATES
    ]
    latest = deployments[0] if deployments else None
    if active and age is not None and age > 1800:
        diagnosis = "POSSIBLE_OVERLAP_BLOCK"
    elif age is None or age > 1800:
        diagnosis = "STALE_DAY_WORKER_STATUS"
    else:
        diagnosis = "DAY_WORKER_STATUS_FRESH"
    return {
        "diagnosis": diagnosis,
        "cron_schedule": cron_schedule,
        "checked_at": checked_at,
        "status_age_seconds": age,
        "worker_source_commit_sha": worker_sha,
        "active_deployment_count": len(active),
        "active_deployments": active[:5],
        "latest_deployment": latest,
        "deployment_count_returned": len(deployments),
    }


def main() -> int:
    token = os.environ["RAILWAY_API_TOKEN"]
    project_id = os.environ["RAILWAY_PROJECT_ID"]
    environment_id = os.environ["RAILWAY_ENVIRONMENT_ID"]
    service_id = os.environ["DAY_RADAR_SERVICE_ID"]
    base = os.environ["PRODUCTION_RADAR_API_BASE_URL"].rstrip("/")
    radar_key = os.environ["PRODUCTION_RADAR_API_KEY"]

    def gql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        request = Request(
            RAILWAY_ENDPOINT,
            data=json.dumps({"query": query, "variables": variables or {}}).encode(),
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "day-worker-scheduler-diag/1",
            },
        )
        with urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode())
        if payload.get("errors"):
            raise RuntimeError(
                json.dumps(
                    [
                        {"message": item.get("message"), "path": item.get("path")}
                        for item in payload["errors"]
                    ],
                    sort_keys=True,
                )
            )
        return payload.get("data") or {}

    def get_day() -> dict[str, Any]:
        request = Request(
            base + "/v1/day-trade/status",
            headers={
                "Accept": "application/json",
                "X-Radar-Key": radar_key,
                "User-Agent": "day-worker-scheduler-diag/1",
            },
        )
        with urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode())
        return payload if isinstance(payload, dict) else {}

    # Railway's public API supports schema introspection. Query the scalar service
    # instance fields first so cronSchedule can be read without guessing unrelated
    # fields or touching variables/secrets.
    cron_schedule: str | None = None
    service_instance_safe: dict[str, Any] = {}
    introspection_error: str | None = None
    try:
        schema = gql(
            """
            query {
              __type(name: "ServiceInstance") {
                fields {
                  name
                  type { kind name ofType { kind name ofType { kind name } } }
                }
              }
            }
            """
        )
        fields = ((schema.get("__type") or {}).get("fields") or [])
        scalar_names: list[str] = []
        allowed = {
            "cronSchedule",
            "startCommand",
            "restartPolicyType",
            "restartPolicyMaxRetries",
            "region",
            "numReplicas",
            "serviceId",
            "environmentId",
            "updatedAt",
        }
        for field in fields:
            if field.get("name") not in allowed:
                continue
            node = field.get("type") or {}
            while node.get("ofType"):
                node = node["ofType"]
            if node.get("kind") in {"SCALAR", "ENUM"}:
                scalar_names.append(str(field["name"]))
        if scalar_names:
            selection = " ".join(sorted(scalar_names))
            instance = gql(
                f"""
                query instance($environmentId: String!, $serviceId: String!) {{
                  serviceInstance(environmentId: $environmentId, serviceId: $serviceId) {{
                    {selection}
                  }}
                }}
                """,
                {"environmentId": environment_id, "serviceId": service_id},
            )
            raw = instance.get("serviceInstance") or {}
            service_instance_safe = {
                key: raw.get(key) for key in scalar_names if key in raw
            }
            cron_schedule = service_instance_safe.get("cronSchedule")
    except Exception as exc:
        introspection_error = f"{type(exc).__name__}: {exc}"

    deployment_error: str | None = None
    deployments_safe: list[dict[str, Any]] = []
    try:
        data = gql(
            """
            query deployments($input: DeploymentListInput!) {
              deployments(input: $input, first: 20) {
                edges { node { id status createdAt meta } }
              }
            }
            """,
            {"input": {"projectId": project_id, "serviceId": service_id}},
        )
        rows = [
            edge.get("node") or {}
            for edge in ((data.get("deployments") or {}).get("edges") or [])
        ]
        for row in rows:
            meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
            deployments_safe.append(
                {
                    "id": row.get("id"),
                    "status": row.get("status"),
                    "created_at": row.get("createdAt"),
                    "commit_sha": meta.get("commitHash")
                    or meta.get("commitSha")
                    or meta.get("commit"),
                }
            )
    except Exception as exc:
        deployment_error = f"{type(exc).__name__}: {exc}"

    day_error: str | None = None
    day: dict[str, Any] = {}
    try:
        day = get_day()
    except Exception as exc:
        day_error = f"{type(exc).__name__}: {exc}"
    worker = day.get("worker") or {}
    summary = summarize_scheduler(
        cron_schedule=cron_schedule,
        deployments=deployments_safe,
        checked_at=day.get("checked_at"),
        worker_sha=worker.get("source_commit_sha"),
    )
    output = {
        "service_id": service_id,
        "environment_id": environment_id,
        "service_instance": service_instance_safe,
        "service_instance_error": introspection_error,
        "deployment_error": deployment_error,
        "day_status_error": day_error,
        "scheduler": summary,
        "recent_deployments": deployments_safe[:12],
    }
    print("DAY_WORKER_SCHEDULER_DIAG=" + json.dumps(output, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
