"""Production smoke for the shared standalone prospective research sidecar."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

POLL_SECONDS = 5
MAX_API_POLLS = 60
MAX_CAPTURE_POLLS = 150
MAX_CAPTURE_AGE_SECONDS = 900
STATE_FILE = Path(".prospective_funnel_deployment.json")
OUTCOME_VISIBILITY = "LOCKED_UNTIL_PREREGISTERED_DEVELOPMENT_GATE"
BARRIER_CONTEXT_VERSION = "day-barrier-clear-context-v1"


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _capture_age(payload: dict[str, Any], now: datetime) -> float | None:
    captured_at = _parse_dt(payload.get("captured_at"))
    return None if captured_at is None else max(0.0, (now - captured_at).total_seconds())


def validate_live_day_status(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the authoritative live day lineage used by research sidecars."""
    errors: list[str] = []
    marker = payload.get("prospective_funnel") or {}
    expected_marker = {
        "status": "EXTERNALIZED",
        "enabled": False,
        "reason": "STANDALONE_RECORDER_OWNS_CAPTURE",
        "execution_mode": "STANDALONE_RAILWAY_CRON",
    }
    for key, expected in expected_marker.items():
        if marker.get(key) != expected:
            errors.append(f"prospective_funnel.{key} mismatch")

    strategy_version = payload.get("strategy_version")
    if not isinstance(strategy_version, str) or not strategy_version:
        errors.append("live strategy_version missing")
        strategy_version = ""
    marker_strategy_version = marker.get("live_strategy_version")
    if not isinstance(marker_strategy_version, str) or not marker_strategy_version:
        errors.append("prospective_funnel.live_strategy_version missing")
    elif strategy_version and marker_strategy_version != strategy_version:
        errors.append("live strategy lineage mismatch")
    return {
        "ok": not errors,
        "errors": errors,
        "strategy_version": strategy_version or None,
        "marker_strategy_version": marker_strategy_version,
    }


def wait_for_live_day_status(
    fetch: Callable[[], dict[str, Any]],
    *,
    max_attempts: int,
    sleep_seconds: float,
) -> dict[str, Any]:
    """Wait for the day worker cache to converge after an exact-main deploy."""
    last: dict[str, Any] = {
        "ok": False,
        "errors": ["live day status not attempted"],
        "strategy_version": None,
        "marker_strategy_version": None,
    }
    for attempt in range(max(1, max_attempts)):
        try:
            last = validate_live_day_status(fetch())
        except Exception as exc:
            last = {
                "ok": False,
                "errors": ["live day status fetch error: " + type(exc).__name__],
                "strategy_version": None,
                "marker_strategy_version": None,
            }
        if last["ok"]:
            return last
        if attempt < max_attempts - 1:
            time.sleep(sleep_seconds)
    return last


def validate_standalone_status(payload: dict[str, Any], expected_sha: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    age = _capture_age(payload, now)
    errors: list[str] = []
    expected = {
        "status": "COMPLETE",
        "research_only": True,
        "label_free": True,
        "outcome_labels_stored": False,
        "spec_version": "v073-prospective-funnel-v1",
        "strategy_version": "0.7.3",
        "execution_mode": "STANDALONE_RAILWAY_CRON",
        "live_worker_inline_recorder": False,
        "live_worker_mutation": False,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            errors.append(f"{key} mismatch")
    if payload.get("source_commit_sha") != expected_sha:
        errors.append("standalone source SHA mismatch")
    if not payload.get("prospective_start_at"):
        errors.append("prospective_start_at missing")
    if age is None or age > MAX_CAPTURE_AGE_SECONDS:
        errors.append("standalone capture stale or missing")
    current = payload.get("current_run") or {}
    cumulative = payload.get("cumulative") or {}
    if not {"observed_snapshots", "inserted_snapshots", "long_snapshots", "short_snapshots"}.issubset(current):
        errors.append("current_run fields missing")
    if not {
        "distinct_sweep_events", "total_snapshots", "exact_live_strict_trigger_events",
        "symbols_observed", "side_event_counts", "latest_gate_pass_counts",
        "latest_first_failed_gate_counts",
    }.issubset(cumulative):
        errors.append("cumulative fields missing")
    return {
        "ok": not errors,
        "errors": errors,
        "source_commit_sha": payload.get("source_commit_sha"),
        "captured_at": payload.get("captured_at"),
        "age_seconds": age,
        "prospective_start_at": payload.get("prospective_start_at"),
        "authoritative_live_scan_as_of": payload.get("authoritative_live_scan_as_of"),
        "authoritative_live_strict_setups": payload.get("authoritative_live_strict_setups"),
        "current_run": current,
        "cumulative": cumulative,
    }


def validate_barrier_parent_status(
    payload: dict[str, Any],
    expected_sha: str,
    current_live_strategy_version: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    age = _capture_age(payload, now)
    errors: list[str] = []
    expected = {
        "status": "COMPLETE",
        "research_only": True,
        "label_free": True,
        "execution_authorized": False,
        "live_strategy_mutation": False,
        "parent_strategy_version": "0.7.5",
        "execution_mode": "SHARED_STANDALONE_RESEARCH_SIDECAR",
        "live_worker_inline_recorder": False,
        "live_worker_mutation": False,
        "outcome_visibility": OUTCOME_VISIBILITY,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            errors.append(f"parent.{key} mismatch")
    if not current_live_strategy_version:
        errors.append("expected live strategy_version missing")
    elif payload.get("current_live_strategy_version") != current_live_strategy_version:
        errors.append("parent.current_live_strategy_version mismatch")
    if payload.get("source_commit_sha") != expected_sha:
        errors.append("parent source SHA mismatch")
    if not payload.get("prospective_start_at"):
        errors.append("parent prospective_start_at missing")
    if age is None or age > MAX_CAPTURE_AGE_SECONDS:
        errors.append("parent capture stale or missing")
    for key in ("admitted_this_run", "inserted_this_run", "total_frozen_parents"):
        value = payload.get(key)
        if not isinstance(value, int) or value < 0:
            errors.append(f"parent {key} invalid")
    return {
        "ok": not errors,
        "errors": errors,
        "source_commit_sha": payload.get("source_commit_sha"),
        "captured_at": payload.get("captured_at"),
        "age_seconds": age,
        "prospective_start_at": payload.get("prospective_start_at"),
        "admitted_this_run": payload.get("admitted_this_run"),
        "inserted_this_run": payload.get("inserted_this_run"),
        "total_frozen_parents": payload.get("total_frozen_parents"),
        "forced_tracking_symbols": payload.get("forced_tracking_symbols") or [],
    }


def validate_barrier_observer_status(
    payload: dict[str, Any],
    expected_sha: str,
    current_live_strategy_version: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    age = _capture_age(payload, now)
    errors: list[str] = []
    expected = {
        "status": "COMPLETE",
        "observer_version": "day-barrier-clear-observer-v1",
        "context_version": BARRIER_CONTEXT_VERSION,
        "research_only": True,
        "label_free": True,
        "execution_authorized": False,
        "live_strategy_mutation": False,
        "score_mutation": False,
        "ranking_mutation": False,
        "eligibility_mutation": False,
        "execution_mutation": False,
        "parent_strategy_version": "0.7.5",
        "execution_mode": "SHARED_STANDALONE_RESEARCH_SIDECAR",
        "live_worker_inline_recorder": False,
        "live_worker_mutation": False,
        "outcome_visibility": OUTCOME_VISIBILITY,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            errors.append(f"observer.{key} mismatch")
    if not current_live_strategy_version:
        errors.append("expected live strategy_version missing")
    elif payload.get("current_live_strategy_version") != current_live_strategy_version:
        errors.append("observer.current_live_strategy_version mismatch")
    if payload.get("source_commit_sha") != expected_sha:
        errors.append("observer source SHA mismatch")
    if age is None or age > MAX_CAPTURE_AGE_SECONDS:
        errors.append("observer capture stale or missing")
    resolved = payload.get("resolved_this_run")
    cumulative = payload.get("cumulative") or {}
    if not isinstance(resolved, dict):
        errors.append("observer resolved_this_run missing")
    if not {"pending", "cleared", "invalidated_boundary", "invalidated_structure"}.issubset(cumulative):
        errors.append("observer cumulative fields missing")
    return {
        "ok": not errors,
        "errors": errors,
        "observer_version": payload.get("observer_version"),
        "context_version": payload.get("context_version"),
        "source_commit_sha": payload.get("source_commit_sha"),
        "captured_at": payload.get("captured_at"),
        "age_seconds": age,
        "resolved_this_run": resolved or {},
        "pending_without_analysis_this_run": payload.get("pending_without_analysis_this_run"),
        "cumulative": cumulative,
        "forced_tracking_symbols": payload.get("forced_tracking_symbols") or [],
    }


def main() -> int:
    base = os.environ["PRODUCTION_RADAR_API_BASE_URL"].rstrip("/")
    key = os.environ["PRODUCTION_RADAR_API_KEY"]
    expected_sha = os.environ["EXPECTED_API_SHA"]

    def get(path: str, *, auth: bool = True, timeout: int = 25) -> dict[str, Any]:
        headers = {"Accept": "application/json", "User-Agent": "standalone-research-smoke/4"}
        if auth:
            headers["X-Radar-Key"] = key
        with urlopen(Request(base + path, headers=headers), timeout=timeout) as response:
            payload = json.loads(response.read().decode())
        if not isinstance(payload, dict):
            raise RuntimeError("non-object payload: " + path)
        return payload

    for attempt in range(MAX_API_POLLS):
        try:
            version = get("/version", auth=False, timeout=15)
            if version.get("commit_sha") == expected_sha:
                print("EXACT_API_SHA=" + expected_sha, flush=True)
                break
        except Exception as exc:
            if attempt % 6 == 0:
                print("API_WAIT=" + type(exc).__name__, flush=True)
        if attempt == MAX_API_POLLS - 1:
            print("FAIL exact tested main API SHA not deployed", flush=True)
            return 1
        time.sleep(POLL_SECONDS)

    live_evidence = wait_for_live_day_status(
        lambda: get("/v1/day-trade/status"),
        max_attempts=MAX_API_POLLS,
        sleep_seconds=POLL_SECONDS,
    )
    if not live_evidence["ok"]:
        print(
            "FAIL live day-worker lineage contract "
            + json.dumps(live_evidence, sort_keys=True),
            flush=True,
        )
        return 1
    live_strategy_version = str(live_evidence["strategy_version"])

    last: dict[str, Any] | None = None
    for attempt in range(MAX_CAPTURE_POLLS):
        try:
            funnel_payload = get("/v1/day-trade/research/prospective-funnel/status")
            parent_payload = get("/v1/day-trade/research/barrier-clear-rearm/parent-status")
            observer_payload = get("/v1/day-trade/research/barrier-clear-rearm/observer-status")
            evidence = {
                "funnel": validate_standalone_status(funnel_payload, expected_sha),
                "barrier_parent": validate_barrier_parent_status(
                    parent_payload, expected_sha, live_strategy_version
                ),
                "barrier_observer": validate_barrier_observer_status(
                    observer_payload, expected_sha, live_strategy_version
                ),
            }
            evidence["ok"] = all(item["ok"] for item in evidence.values())
            last = evidence
            if attempt % 6 == 0:
                print("STANDALONE_PROGRESS=" + json.dumps(evidence, sort_keys=True, default=str), flush=True)
            if evidence["ok"]:
                print("STANDALONE_PROSPECTIVE_RESEARCH_EVIDENCE=" + json.dumps(evidence, sort_keys=True, default=str), flush=True)
                print("SHARED STANDALONE PROSPECTIVE RESEARCH PRODUCTION VERIFIED.", flush=True)
                return 0
        except Exception as exc:
            if attempt % 6 == 0:
                print("CAPTURE_WAIT=" + type(exc).__name__, flush=True)
        if attempt < MAX_CAPTURE_POLLS - 1:
            time.sleep(POLL_SECONDS)

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    print("FAIL no qualifying standalone prospective research capture: " + json.dumps({"evidence": last, "deployment": state}, sort_keys=True, default=str), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
