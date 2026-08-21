#!/usr/bin/env python3
"""Run one natural swing-liquidity capture and fail closed on lifecycle semantics.

Research only. This is a thin orchestration layer around the existing collector and
persistence API. It does not create a second verification capture: the exact response
from the one scheduled capture is validated before the workflow may continue.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from research.swing_liquidity_shadow import collect_snapshot, persist_snapshot
from scripts.production_swing_liquidity_lifecycle_smoke import validate_lifecycle_persistence


def run_capture(
    base_url: str,
    api_key: str,
    bybit_base_url: str,
    output: Path,
    *,
    collect: Callable[[str, str, str], dict[str, Any]] = collect_snapshot,
    persist: Callable[[str, str, dict[str, Any]], dict[str, Any]] = persist_snapshot,
) -> int:
    snapshot = collect(base_url, api_key, bybit_base_url)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
    result = persist(base_url, api_key, snapshot)

    lifecycle_errors = validate_lifecycle_persistence(result)
    lifecycle = result.get("lifecycle_adoption") if isinstance(result, dict) else None
    safe = {
        "captured_at": snapshot.get("captured_at"),
        "feature_available_at": snapshot.get("feature_available_at"),
        "trial_fingerprint": snapshot.get("trial_fingerprint"),
        "scan_data_as_of": snapshot.get("scan_data_as_of"),
        "candidate_count": snapshot.get("candidate_count"),
        "orderbook_count": len(snapshot.get("orderbooks") or {}),
        "orderbook_error_count": len(snapshot.get("orderbook_errors") or {}),
        "durable_inserted": result.get("inserted") if isinstance(result, dict) else None,
        "lifecycle_event_type": lifecycle.get("event_type") if isinstance(lifecycle, dict) else None,
        "lifecycle_reason": lifecycle.get("reason") if isinstance(lifecycle, dict) else None,
        "lifecycle_transition_inserted": lifecycle.get("inserted") if isinstance(lifecycle, dict) else None,
    }
    print("SWING_LIQUIDITY_SHADOW_GUARDED=" + json.dumps(safe, sort_keys=True, default=str))
    if lifecycle_errors:
        for error in lifecycle_errors:
            print(f"FAIL lifecycle_{error}")
        return 1
    print("SWING LIQUIDITY NATURAL CAPTURE LIFECYCLE RESPONSE VERIFIED.")
    return 0


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    bybit_base = os.getenv("BYBIT_EU_PUBLIC_BASE_URL", "https://api.bybit.eu").strip()
    output = Path(os.getenv("SWING_LIQUIDITY_SHADOW_OUTPUT", "swing-liquidity-shadow.json"))
    if not base_url or not api_key:
        print("FAIL required production configuration is missing")
        return 1
    try:
        return run_capture(base_url, api_key, bybit_base, output)
    except Exception as exc:
        print(f"FAIL request_error={type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
