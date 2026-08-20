"""Read-only comparison of old/new standalone recorder Railway deployments."""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.request import Request, urlopen

ENDPOINT = "https://backboard.railway.com/graphql/v2"
DEPLOYMENTS = (
    "f1088a77-6174-45e1-8e05-75582d188fe1",  # prior healthy/stale runtime deployment
    "82faebd6-cf6d-48f8-861c-30d00ef4aa47",  # exact 9776fb21 deployment
)


def gql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    req = Request(
        ENDPOINT,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": "Bearer " + os.environ["RAILWAY_API_TOKEN"],
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "recorder-ws403-diagnostic/1",
        },
    )
    with urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode())
    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"]))
    return payload.get("data") or {}


def main() -> int:
    deployment_query = """
    query deployment($id:String!){deployment(id:$id){id status createdAt meta canRedeploy canRollback}}
    """
    logs_query = """
    query logs($id:String!){deploymentLogs(deploymentId:$id,limit:120){timestamp message severity}}
    """
    for deployment_id in DEPLOYMENTS:
        deployment = gql(deployment_query, {"id": deployment_id}).get("deployment") or {}
        meta = dict(deployment.get("meta") or {})
        safe = {
            "id": deployment.get("id"),
            "status": deployment.get("status"),
            "createdAt": deployment.get("createdAt"),
            "canRedeploy": deployment.get("canRedeploy"),
            "canRollback": deployment.get("canRollback"),
            "meta": {
                key: meta.get(key)
                for key in (
                    "repo", "branch", "commitHash", "rootDirectory", "runtime", "serviceManifest",
                    "nixpacksProviders", "plan", "reason", "volumeMounts"
                )
                if key in meta
            },
        }
        print("DEPLOYMENT=" + json.dumps(safe, sort_keys=True))
        logs = gql(logs_query, {"id": deployment_id}).get("deploymentLogs") or []
        filtered = [
            item for item in logs
            if any(token in str(item.get("message") or "").lower() for token in ("websocket", "403", "recorder", "error", "connected", "starting"))
        ]
        print("RUNTIME_LOGS=" + json.dumps(filtered[-80:], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
