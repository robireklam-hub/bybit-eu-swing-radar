"""Read-only Railway source/deployment diagnostics for the standalone microstructure recorder."""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.request import Request, urlopen

ENDPOINT = "https://backboard.railway.com/graphql/v2"
INTERESTING = ("source", "repo", "branch", "deploy", "root", "start", "build")


def gql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    req = Request(
        ENDPOINT,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={
            "Authorization": "Bearer " + os.environ["RAILWAY_API_TOKEN"],
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "microstructure-recorder-source-diagnostic/1",
        },
    )
    with urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode())
    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"]))
    return payload.get("data") or {}


def type_fields(type_name: str) -> list[dict[str, Any]]:
    query = """
    query schema($name:String!){
      __type(name:$name){fields{name args{name} type{kind name ofType{kind name ofType{kind name}}}}}
    }
    """
    return (((gql(query, {"name": type_name}).get("__type") or {}).get("fields")) or [])


def base_type(field: dict[str, Any]) -> tuple[str, str]:
    node = field.get("type") or {}
    while node.get("ofType"):
        node = node["ofType"]
    return str(node.get("kind") or ""), str(node.get("name") or "")


def main() -> int:
    service_id = os.environ["MICROSTRUCTURE_RECORDER_SERVICE_ID"]
    environment_id = os.environ["RAILWAY_ENVIRONMENT_ID"]

    for type_name in ("Service", "ServiceInstance", "Deployment"):
        safe_schema = []
        for field in type_fields(type_name):
            name = str(field.get("name") or "")
            if any(token in name.lower() for token in INTERESTING):
                kind, nested = base_type(field)
                safe_schema.append({"name": name, "kind": kind, "type": nested, "args": [a.get("name") for a in field.get("args") or []]})
        print(type_name.upper() + "_INTERESTING_FIELDS=" + json.dumps(safe_schema, sort_keys=True))

    instance_query = """
    query instance($serviceId:String!,$environmentId:String!){
      serviceInstance(serviceId:$serviceId,environmentId:$environmentId){
        id serviceName startCommand buildCommand rootDirectory region restartPolicyType
        source
        latestDeployment{id status createdAt meta}
      }
    }
    """
    instance = gql(instance_query, {"serviceId": service_id, "environmentId": environment_id}).get("serviceInstance") or {}
    print("SERVICE_INSTANCE_STATE=" + json.dumps(instance, sort_keys=True))

    deployment = instance.get("latestDeployment") or {}
    deployment_id = str(deployment.get("id") or "")
    if deployment_id:
        build_query = "query logs($deploymentId:String!){buildLogs(deploymentId:$deploymentId,limit:200){timestamp message severity}}"
        runtime_query = "query logs($deploymentId:String!){deploymentLogs(deploymentId:$deploymentId,limit:200){timestamp message severity}}"
        build_logs = gql(build_query, {"deploymentId": deployment_id}).get("buildLogs") or []
        runtime_logs = gql(runtime_query, {"deploymentId": deployment_id}).get("deploymentLogs") or []
        print("LATEST_DEPLOYMENT_BUILD_LOGS=" + json.dumps(build_logs, sort_keys=True))
        print("LATEST_DEPLOYMENT_RUNTIME_LOGS=" + json.dumps(runtime_logs, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
