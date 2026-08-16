"""Dispatch and monitor production historical flow research v2."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = os.environ.get("PRODUCTION_RADAR_API_BASE_URL", "").rstrip("/")
KEY = os.environ.get("PRODUCTION_RADAR_API_KEY", "")
POLL_SECONDS = 10
TIMEOUT_SECONDS = 60 * 60

STATUS_PATH = "/v1/day-trade/research/flow/v2/status"
RUN_PATH = "/v1/day-trade/research/flow/v2/run"
REPORT_PATH = "/v1/day-trade/research/flow/v2/report"


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
            f"Historical flow v2 API HTTP {exc.code} {path}: {body[:1200]}"
        ) from exc


def compact(status: dict) -> str:
    coverage = status.get("coverage") or {}
    return (
        f"status={status.get('status')} rows={status.get('rows')} "
        f"oi_coverage={coverage.get('oi_coverage_pct')} "
        f"funding_coverage={coverage.get('funding_coverage_pct')} "
        f"both_coverage={coverage.get('both_coverage_pct')}"
    )


def main() -> int:
    initial = request_json(STATUS_PATH)
    print("INITIAL " + compact(initial))
    if initial.get("status") != "COMPLETED":
        accepted = request_json(RUN_PATH, method="POST")
        print("ACCEPTED=" + json.dumps(accepted, sort_keys=True))

    deadline = time.monotonic() + TIMEOUT_SECONDS
    while True:
        status = request_json(STATUS_PATH)
        print("POLL " + compact(status))
        state = str(status.get("status") or "")
        if state == "COMPLETED":
            break
        if state == "FAILED":
            raise RuntimeError(f"Historical flow v2 research failed: {status.get('error')}")
        if time.monotonic() >= deadline:
            raise RuntimeError("Timed out waiting for historical flow v2 research")
        time.sleep(POLL_SECONDS)

    report = request_json(REPORT_PATH)
    print("HISTORICAL_FLOW_V2_REPORT=" + json.dumps(report, sort_keys=True, default=str))
    coverage = report.get("coverage") or {}
    analysis = report.get("analysis") or {}
    holdout = analysis.get("internal_holdout_result") or {}
    selected = holdout.get("selected") or {}
    print(
        "HISTORICAL_FLOW_V2_SUMMARY "
        f"rows={coverage.get('rows')} "
        f"oi_coverage={coverage.get('oi_coverage_pct')} "
        f"funding_coverage={coverage.get('funding_coverage_pct')} "
        f"both_coverage={coverage.get('both_coverage_pct')} "
        f"winner={analysis.get('selected_on_train')} "
        f"holdout_n={selected.get('n')} "
        f"holdout_avg_r={selected.get('average_net_r')} "
        f"holdout_pf={selected.get('profit_factor')} "
        f"edge_pass={analysis.get('internal_holdout_edge_pass')} "
        f"promotion_allowed={analysis.get('promotion_allowed')}"
    )
    if not coverage.get("rows"):
        raise RuntimeError("Historical flow v2 report contains no rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
