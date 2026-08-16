"""Dispatch and monitor production breakout-continuation research v5."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = os.environ.get("PRODUCTION_RADAR_API_BASE_URL", "").rstrip("/")
KEY = os.environ.get("PRODUCTION_RADAR_API_KEY", "")
POLL_SECONDS = 10
TIMEOUT_SECONDS = 60 * 80
STATUS_PATH = "/v1/day-trade/research/breakout/v5/status"
RUN_PATH = "/v1/day-trade/research/breakout/v5/run"
REPORT_PATH = "/v1/day-trade/research/breakout/v5/report"


def request_json(path: str, *, method: str = "GET") -> dict:
    if not BASE or not KEY:
        raise RuntimeError("Production API base URL/key are required")
    request = urllib.request.Request(BASE + path, method=method, headers={"X-Radar-Key": KEY})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Breakout v5 API HTTP {exc.code} {path}: {body[:1200]}") from exc


def compact(status: dict) -> str:
    return (
        f"status={status.get('status')} events={status.get('events')} "
        f"winner={status.get('winner')} holdout_edge_pass={status.get('holdout_edge_pass')}"
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
            raise RuntimeError(f"Breakout v5 research failed: {status.get('error')}")
        if time.monotonic() >= deadline:
            raise RuntimeError("Timed out waiting for breakout v5 research")
        time.sleep(POLL_SECONDS)

    report = request_json(REPORT_PATH)
    print("BREAKOUT_V5_REPORT=" + json.dumps(report, sort_keys=True, default=str))
    analysis = report.get("analysis") or {}
    holdout = analysis.get("internal_holdout_result") or {}
    print(
        "BREAKOUT_V5_SUMMARY "
        f"events={report.get('events')} "
        f"winner={analysis.get('selected_on_train')} "
        f"holdout_n={holdout.get('n')} "
        f"holdout_avg_r={holdout.get('average_net_r')} "
        f"holdout_pf={holdout.get('profit_factor')} "
        f"family_edge_pass={analysis.get('strategy_family_edge_pass')} "
        f"promotion_allowed={analysis.get('promotion_allowed')}"
    )
    if not report.get("events"):
        raise RuntimeError("Breakout v5 report contains no events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
