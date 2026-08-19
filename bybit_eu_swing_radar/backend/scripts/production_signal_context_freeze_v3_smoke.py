#!/usr/bin/env python3
"""Exact-SHA production smoke for prospective Signal-Time Freeze v3."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from typing import Any
from urllib.request import Request, urlopen


def _request(base: str, path: str, key: str, *, method: str = "GET", timeout: float = 45.0) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "signal-freeze-v3-production-smoke/1"}
    if key:
        headers["X-Radar-Key"] = key
    request = Request(base.rstrip("/") + path, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode())
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def main() -> int:
    base = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    key = os.getenv("PRODUCTION_RADAR_API_KEY", "").strip()
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base or not key or not expected_sha:
        print("FAIL missing production smoke configuration")
        return 1

    for attempt in range(45):
        try:
            if _request(base, "/version", "", timeout=15).get("commit_sha") == expected_sha:
                break
        except Exception:
            pass
        if attempt == 44:
            print("FAIL exact production API SHA not serving")
            return 1
        time.sleep(4)

    spec = _request(base, "/v1/research/signal-context-freeze-v3/spec", key, timeout=20)
    if spec.get("version") != "signal-context-freeze-v3":
        print("FAIL unexpected signal freeze v3 spec")
        return 1
    if (spec.get("source_layers") or {}).get("cross_layer_context") != "cross-layer-context-shadow-v2":
        print("FAIL signal freeze v3 is not bound to Cross-Layer v2")
        return 1
    if spec.get("historical_backfill_allowed") is not False or spec.get("v1_preserved") is not True or spec.get("v2_preserved") is not True or spec.get("immutable_history_required") is not True:
        print("FAIL prospective/version-isolation contract changed")
        return 1
    for field, expected in {
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "execution_proof": False,
    }.items():
        if spec.get(field) != expected:
            print(f"FAIL spec guard changed: {field}")
            return 1

    before = _request(base, "/v1/research/signal-context-freeze-v3/status", key, timeout=25)
    capture = _request(base, "/v1/research/signal-context-freeze-v3/capture", key, method="POST", timeout=55)
    after = _request(base, "/v1/research/signal-context-freeze-v3/status", key, timeout=25)

    safe = {
        "source_commit_sha": capture.get("source_commit_sha"),
        "prospective_start_source": capture.get("prospective_start_source"),
        "cross_layer_lookup_source": capture.get("cross_layer_lookup_source"),
        "prospective_start_at": capture.get("prospective_start_at"),
        "signals_examined": capture.get("signals_examined"),
        "inserted": capture.get("inserted"),
        "cross_layer_status_counts": capture.get("cross_layer_status_counts"),
        "microstructure_status_counts": capture.get("microstructure_status_counts"),
        "pre_v3_journal_signals_excluded": after.get("pre_v3_journal_signals_excluded"),
        "prospective_journal_signal_count": after.get("prospective_journal_signal_count"),
        "frozen_signal_count": after.get("frozen_signal_count"),
        "freeze_coverage_pct": after.get("freeze_coverage_pct"),
        "first_opened_at": (after.get("counts") or {}).get("first_opened_at"),
    }
    print("SIGNAL_CONTEXT_FREEZE_V3=" + json.dumps(safe, sort_keys=True))

    if capture.get("prospective_start_source") != "immutable_raw_history_v1" or capture.get("cross_layer_lookup_source") != "research_snapshot_history":
        print("FAIL v3 is not bound to immutable raw history")
        return 1
    if capture.get("source_commit_sha") != expected_sha:
        print("FAIL signal freeze v3 capture not on exact SHA")
        return 1
    if capture.get("historical_backfill_allowed") is not False:
        print("FAIL capture permits historical backfill")
        return 1
    start = _dt(capture.get("prospective_start_at"))
    if start is None:
        print("FAIL no prospective Cross-Layer v2 start boundary")
        return 1
    if before.get("prospective_start_at") != capture.get("prospective_start_at") or after.get("prospective_start_at") != capture.get("prospective_start_at"):
        print("FAIL prospective start boundary is unstable")
        return 1
    first_opened = _dt((after.get("counts") or {}).get("first_opened_at"))
    if first_opened is not None and first_opened < start:
        print("FAIL pre-v3 signal leaked into v3 freeze cohort")
        return 1
    if after.get("historical_backfill_allowed") is not False or after.get("outcome_fields_read") is not False:
        print("FAIL status safety contract changed")
        return 1
    if int(after.get("frozen_signal_count") or 0) > int(after.get("prospective_journal_signal_count") or 0):
        print("FAIL v3 frozen count exceeds prospective journal population")
        return 1
    if after.get("promotion_allowed") is not False or after.get("live_strategy_mutated") is not False:
        print("FAIL live/promotion guard changed")
        return 1

    print("SIGNAL-TIME CONTEXT FREEZE V3 VERIFIED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
