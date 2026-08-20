"""Deploy the isolated microstructure recorder on one exact tested main commit.

Trusted main-only workflow utility. It does not mutate recorder ownership,
region, variables, or live strategy state; it only requests an exact-commit
deployment of the already-provisioned standalone service and waits for the
requested deployment to reach a terminal state.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENDPOINT = "https://backboard.railway.com/graphql/v2"
DEPLOYMENT_POLL_SECONDS = 5
DEPLOYMENT_MAX_WAIT_SECONDS = 900
GQL_RETRY_ATTEMPTS = 4
TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}
TERMINAL_FAILURES = {"FAILED", "CRASHED", "REMOVED", "CANCELLED"}


def _gql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    token = os.environ["RAILWAY_API_TOKEN"]
    request = Request(
        ENDPOINT,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "microstructure-recorder-exact-main-deployer/1",
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
        except (URLError, TimeoutError):
            if attempt + 1 >= GQL_RETRY_ATTEMPTS:
                raise
        time.sleep(min(2 ** attempt, 8))
    raise RuntimeError("Railway GraphQL retry loop exhausted")


def request_exact_deployment(environment_id: str, service_id: str, commit_sha: str) -> str:
    if len(commit_sha) != 40 or any(character not in "0123456789abcdef" for character in commit_sha.lower()):
        raise ValueError("EXPECTED_SHA must be a full 40-character hexadecimal commit SHA")
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
        raise RuntimeError("Railway exact-commit deploy returned no deployment id")
    return deployment_id


def deployment_status(deployment_id: str) -> str:
    query = "query deployment($id:String!){deployment(id:$id){id status}}"
    deployment = _gql(query, {"id": deployment_id}).get("deployment") or {}
    return str(deployment.get("status") or "UNKNOWN").upper()


def wait_for_success(deployment_id: str) -> str:
    attempts = max(1, DEPLOYMENT_MAX_WAIT_SECONDS // DEPLOYMENT_POLL_SECONDS)
    final_status = "UNKNOWN"
    for attempt in range(attempts):
        final_status = deployment_status(deployment_id)
        if attempt % 6 == 0 or final_status in {"SUCCESS", *TERMINAL_FAILURES}:
            print("MICROSTRUCTURE_RECORDER_DEPLOYMENT_STATUS=" + final_status, flush=True)
        if final_status == "SUCCESS":
            return final_status
        if final_status in TERMINAL_FAILURES:
            raise RuntimeError("Exact microstructure recorder deployment ended: " + final_status)
        time.sleep(DEPLOYMENT_POLL_SECONDS)
    raise RuntimeError(
        "Exact microstructure recorder deployment did not reach SUCCESS within "
        + str(DEPLOYMENT_MAX_WAIT_SECONDS)
        + "s; final_status="
        + final_status
    )


def main() -> int:
    environment_id = os.environ["RAILWAY_ENVIRONMENT_ID"]
    service_id = os.environ["MICROSTRUCTURE_RECORDER_SERVICE_ID"]
    commit_sha = os.environ["EXPECTED_SHA"].strip().lower()

    deployment_id = request_exact_deployment(environment_id, service_id, commit_sha)
    print("MICROSTRUCTURE_RECORDER_SERVICE_ID=" + service_id, flush=True)
    print("MICROSTRUCTURE_RECORDER_DEPLOYMENT_ID=" + deployment_id, flush=True)
    print("MICROSTRUCTURE_RECORDER_EXPECTED_SHA=" + commit_sha, flush=True)
    wait_for_success(deployment_id)
    print("MICROSTRUCTURE RECORDER EXACT-COMMIT DEPLOYMENT SUCCESS.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
