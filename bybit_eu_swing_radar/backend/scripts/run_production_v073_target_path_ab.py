"""Dispatch and monitor production v0.7.3 target-path A/B research replay."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = os.environ.get("PRODUCTION_RADAR_API_BASE_URL", "").rstrip("/")
KEY = os.environ.get("PRODUCTION_RADAR_API_KEY", "")
BATCH_COUNT = max(1, min(int(os.environ.get("TARGET_PATH_AB_BATCH_COUNT", "1")), 15))
POLL_SECONDS = 10
ADVANCE_TIMEOUT_SECONDS = 20 * 60

STATUS_PATH = "/v1/day-trade/backtest/target-path-ab/v073/status"
RUN_PATH = "/v1/day-trade/backtest/target-path-ab/v073/run-batch"
REPORT_PATH = "/v1/day-trade/backtest/target-path-ab/v073/report"


def request_json(path: str, *, method: str = "GET") -> dict:
    if not BASE or not KEY:
        raise RuntimeError("Production API base URL/key are required")
    request = urllib.request.Request(
        BASE + path,
        method=method,
        headers={"X-Radar-Key": KEY},
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Target-path A/B API HTTP {exc.code} {path}: {body[:1200]}"
        ) from exc


def terminal_count(status: dict) -> int:
    job = status.get("job") or {}
    return int(job.get("completed_symbols") or 0) + int(job.get("failed_symbols") or 0)


def compact_status(status: dict) -> str:
    job = status.get("job") or {}
    return (
        f"status={job.get('status')} completed={job.get('completed_symbols')} "
        f"failed={job.get('failed_symbols')} total={job.get('total_symbols')} "
        f"progress={status.get('progress_pct')}"
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
        print(
            f"BATCH {batch_index}/{BATCH_COUNT} accepted="
            + json.dumps(accepted, sort_keys=True)
        )
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
                raise RuntimeError(
                    f"Timed out waiting for target-path A/B batch {batch_index} to advance"
                )

    status = request_json(STATUS_PATH)
    print("FINAL_STATUS=" + json.dumps(status, sort_keys=True, default=str))
    job = status.get("job") or {}
    if job.get("status") in {"COMPLETED", "PARTIAL"}:
        report = request_json(REPORT_PATH)
        print("V073_TARGET_PATH_AB_REPORT=" + json.dumps(report, sort_keys=True, default=str))
        hypothesis = ((report.get("hypothesis_criteria") or {}).get("decision"))
        production = ((report.get("production_criteria") or {}).get("decision"))
        a = (((report.get("models") or {}).get("A_CURRENT_BARRIER") or {}).get("overall") or {})
        b = (((report.get("models") or {}).get("B_ACTIVE_BARRIER") or {}).get("overall") or {})
        delta = report.get("ab_delta") or {}
        print(
            "AB_SUMMARY "
            f"hypothesis={hypothesis} production={production} "
            f"A_n={a.get('sample_size')} A_avg_r={a.get('average_net_r')} A_pf={a.get('profit_factor')} "
            f"B_n={b.get('sample_size')} B_avg_r={b.get('average_net_r')} B_pf={b.get('profit_factor')} "
            f"stale_removed={delta.get('stale_barriers_removed')} "
            f"paths_recovered={delta.get('target_paths_recovered')}"
        )
        blocks = (((report.get("models") or {}).get("B_ACTIVE_BARRIER") or {}).get("blocks_30d") or [])
        for block in blocks:
            print(
                "B_BLOCK "
                f"index={block.get('index')} n={block.get('sample_size')} "
                f"avg_r={block.get('average_net_r')} pf={block.get('profit_factor')} "
                f"total_r={block.get('total_net_r')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
