"""Dispatch and monitor production generation of research dataset v1."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = os.environ.get("PRODUCTION_RADAR_API_BASE_URL", "").rstrip("/")
KEY = os.environ.get("PRODUCTION_RADAR_API_KEY", "")
BATCH_COUNT = max(1, min(int(os.environ.get("RESEARCH_DATASET_BATCH_COUNT", "15")), 15))
POLL_SECONDS = 10
ADVANCE_TIMEOUT_SECONDS = 20 * 60

STATUS_PATH = "/v1/day-trade/research/dataset/v1/status"
RUN_PATH = "/v1/day-trade/research/dataset/v1/run-batch"
REPORT_PATH = "/v1/day-trade/research/dataset/v1/report"

def request_json(path: str, *, method: str = "GET") -> dict:
    if not BASE or not KEY:
        raise RuntimeError("Production API base URL/key are required")
    request = urllib.request.Request(BASE + path, method=method, headers={"X-Radar-Key": KEY})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Research dataset API HTTP {exc.code} {path}: {body[:1200]}") from exc

def terminal_count(status: dict) -> int:
    job = status.get("job") or {}
    return int(job.get("completed_symbols") or 0) + int(job.get("failed_symbols") or 0)

def compact_status(status: dict) -> str:
    job = status.get("job") or {}
    return (
        f"status={job.get('status')} completed={job.get('completed_symbols')} "
        f"failed={job.get('failed_symbols')} total={job.get('total_symbols')} "
        f"events={job.get('total_events')} progress={status.get('progress_pct')}"
    )

def main() -> int:
    status = request_json(STATUS_PATH)
    print("INITIAL " + compact_status(status))
    for batch_index in range(1, BATCH_COUNT + 1):
        job = status.get("job") or {}
        if job.get("status") in {"COMPLETED", "PARTIAL", "FAILED"}:
            break
        before = terminal_count(status)
        accepted = request_json(RUN_PATH, method="POST")
        print(f"BATCH {batch_index}/{BATCH_COUNT} accepted=" + json.dumps(accepted, sort_keys=True))
        deadline = time.monotonic() + ADVANCE_TIMEOUT_SECONDS
        while True:
            time.sleep(POLL_SECONDS)
            status = request_json(STATUS_PATH)
            print("POLL " + compact_status(status))
            job = status.get("job") or {}
            if job.get("status") in {"COMPLETED", "PARTIAL", "FAILED"}:
                break
            if terminal_count(status) > before:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Timed out waiting for research dataset batch {batch_index} to advance")

    status = request_json(STATUS_PATH)
    print("FINAL_STATUS=" + json.dumps(status, sort_keys=True, default=str))
    job = status.get("job") or {}
    if job.get("status") in {"COMPLETED", "PARTIAL"}:
        report = request_json(REPORT_PATH)
        print("RESEARCH_DATASET_REPORT=" + json.dumps(report, sort_keys=True, default=str))
        counts = report.get("counts") or {}
        discovery = ((report.get("baseline") or {}).get("discovery") or {})
        validation = ((report.get("baseline") or {}).get("validation") or {})
        print(
            "RESEARCH_DATASET_SUMMARY "
            f"materialized={counts.get('materialized_opportunities')} "
            f"evaluable={counts.get('outcome_evaluable')} "
            f"discovery={counts.get('discovery_evaluable')} "
            f"validation={counts.get('validation_evaluable')} "
            f"discovery_avg_r={discovery.get('average_net_r')} discovery_pf={discovery.get('profit_factor')} "
            f"validation_avg_r={validation.get('average_net_r')} validation_pf={validation.get('profit_factor')}"
        )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
