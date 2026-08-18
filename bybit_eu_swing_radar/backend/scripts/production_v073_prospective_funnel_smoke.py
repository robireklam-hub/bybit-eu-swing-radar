"""Read-only production smoke for the v0.7.3 prospective funnel recorder.

The smoke runs only from a trusted main-branch workflow. It proves:
- the production API serves the exact tested main SHA;
- the day worker runs a commit that contains the prospective-funnel feature SHA;
- the recorder reports COMPLETE from that same worker SHA;
- the research-only / label-free contract is intact.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.request import Request, urlopen

POLL_SECONDS = 5
MAX_API_POLLS = 45
MAX_WORKER_POLLS = 180
MAX_STATUS_AGE_SECONDS = 1800


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def commit_contains_feature(feature_sha: str, worker_sha: str) -> bool:
    """Return true when worker_sha is feature_sha or a descendant in git history."""
    if not feature_sha or not worker_sha:
        return False
    if feature_sha == worker_sha:
        return True
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", feature_sha, worker_sha],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def validate_funnel_status(
    day: dict[str, Any],
    *,
    feature_sha: str,
    now: datetime | None = None,
    ancestry_check: Callable[[str, str], bool] = commit_contains_feature,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    worker = day.get("worker") or {}
    funnel = day.get("prospective_funnel") or {}
    worker_sha = str(worker.get("source_commit_sha") or "")
    funnel_sha = str(funnel.get("source_commit_sha") or "")
    checked_at = _parse_dt(day.get("checked_at"))
    age_seconds = None if checked_at is None else max(0.0, (now - checked_at).total_seconds())

    errors: list[str] = []
    if not ancestry_check(feature_sha, worker_sha):
        errors.append("worker commit does not contain prospective-funnel feature SHA")
    if funnel_sha != worker_sha:
        errors.append("funnel source SHA does not match worker source SHA")
    if age_seconds is None or age_seconds > MAX_STATUS_AGE_SECONDS:
        errors.append("day-worker status is stale or missing checked_at")
    if funnel.get("status") != "COMPLETE":
        errors.append("prospective funnel status is not COMPLETE")
    if funnel.get("research_only") is not True:
        errors.append("research_only is not true")
    if funnel.get("label_free") is not True:
        errors.append("label_free is not true")
    if funnel.get("outcome_labels_stored") is not False:
        errors.append("outcome_labels_stored is not false")
    if funnel.get("spec_version") != "v073-prospective-funnel-v1":
        errors.append("spec_version mismatch")
    if str(funnel.get("strategy_version") or "") != "0.7.3":
        errors.append("strategy_version mismatch")
    if not funnel.get("prospective_start_at"):
        errors.append("prospective_start_at missing")

    current = funnel.get("current_run") or {}
    cumulative = funnel.get("cumulative") or {}
    required_current = {
        "observed_snapshots",
        "inserted_snapshots",
        "long_snapshots",
        "short_snapshots",
    }
    if not required_current.issubset(current):
        errors.append("current_run fields missing")
    required_cumulative = {
        "distinct_sweep_events",
        "total_snapshots",
        "exact_live_strict_trigger_events",
        "symbols_observed",
        "side_event_counts",
        "latest_gate_pass_counts",
        "latest_first_failed_gate_counts",
    }
    if not required_cumulative.issubset(cumulative):
        errors.append("cumulative fields missing")

    return {
        "ok": not errors,
        "errors": errors,
        "worker_source_commit_sha": worker_sha or None,
        "funnel_source_commit_sha": funnel_sha or None,
        "checked_at": day.get("checked_at"),
        "age_seconds": age_seconds,
        "funnel_status": funnel.get("status"),
        "prospective_start_at": funnel.get("prospective_start_at"),
        "current_run": current,
        "cumulative": cumulative,
    }


def main() -> int:
    base = os.environ["PRODUCTION_RADAR_API_BASE_URL"].rstrip("/")
    key = os.environ["PRODUCTION_RADAR_API_KEY"]
    expected_api_sha = os.environ["EXPECTED_API_SHA"]
    feature_sha = os.environ["PROSPECTIVE_FUNNEL_FEATURE_SHA"]

    def get(path: str, *, auth: bool = True, timeout: int = 25) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "v073-prospective-funnel-production-smoke/1",
        }
        if auth:
            headers["X-Radar-Key"] = key
        with urlopen(Request(base + path, headers=headers), timeout=timeout) as response:
            payload = json.loads(response.read().decode())
        if not isinstance(payload, dict):
            raise RuntimeError("non-object API payload for " + path)
        return payload

    api_ok = False
    for attempt in range(MAX_API_POLLS):
        try:
            version = get("/version", auth=False, timeout=15)
            if version.get("commit_sha") == expected_api_sha:
                api_ok = True
                print("EXACT_API_SHA=" + expected_api_sha, flush=True)
                break
        except Exception as exc:  # bounded deploy wait
            if attempt % 6 == 0:
                print("API_WAIT=" + type(exc).__name__, flush=True)
        if attempt < MAX_API_POLLS - 1:
            time.sleep(POLL_SECONDS)
    if not api_ok:
        print("FAIL exact tested main API SHA not deployed", flush=True)
        return 1

    last_evidence: dict[str, Any] | None = None
    for attempt in range(MAX_WORKER_POLLS):
        try:
            day = get("/v1/day-trade/status")
            evidence = validate_funnel_status(day, feature_sha=feature_sha)
            last_evidence = evidence
            if attempt % 6 == 0:
                print(
                    "FUNNEL_VERIFY_PROGRESS="
                    + json.dumps(evidence, sort_keys=True, default=str),
                    flush=True,
                )
            if evidence["ok"]:
                print(
                    "PROSPECTIVE_FUNNEL_EVIDENCE="
                    + json.dumps(evidence, sort_keys=True, default=str),
                    flush=True,
                )
                print("V0.7.3 PROSPECTIVE FUNNEL PRODUCTION VERIFIED.", flush=True)
                return 0
        except Exception as exc:  # bounded worker wait
            if attempt % 6 == 0:
                print("WORKER_WAIT=" + type(exc).__name__, flush=True)
        if attempt < MAX_WORKER_POLLS - 1:
            time.sleep(POLL_SECONDS)

    print(
        "FAIL no qualifying prospective-funnel day-worker run observed: "
        + json.dumps(last_evidence, sort_keys=True, default=str),
        flush=True,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
