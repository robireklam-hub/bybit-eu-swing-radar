#!/usr/bin/env python3
"""Fail-closed natural swing-liquidity capture with exact API/worker lineage parity.

Research only. The durable capture table records the production API commit SHA as
``source_commit_sha``. During rolling deploys the API and swing worker can briefly
serve different commits, which would mislabel forward research lineage.

The scan cache can also rotate while order books are being collected. This wrapper
brackets collection with worker-status observations. It accepts either an exact
status identity match or a bounded in-window scan rotation when both surrounding
worker observations are valid, ordered, and report the same commit as the API.
No live score, eligibility, threshold, or execution state is changed.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from research.swing_liquidity_shadow import collect_snapshot  # noqa: E402
from scripts.run_swing_liquidity_shadow_guarded import run_capture  # noqa: E402


def _get_json(url: str, api_key: str | None = None, timeout: float = 20.0) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "swing-liquidity-worker-lineage/3"}
    if api_key:
        headers["X-Radar-Key"] = api_key
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("lineage response is not an object")
    return payload


def _valid_commit_sha(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 40 and all(char in "0123456789abcdef" for char in text)


def _parse_timestamp(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"swing liquidity lineage unavailable: {field} missing")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"swing liquidity lineage unavailable: {field} invalid") from exc
    if parsed.tzinfo is None:
        raise RuntimeError(f"swing liquidity lineage unavailable: {field} timezone missing")
    return parsed


def _worker_observation(status: dict[str, Any], phase: str) -> tuple[str, str, datetime, str]:
    checked_at = str(status.get("checked_at") or "").strip()
    checked_dt = _parse_timestamp(checked_at, f"{phase} worker checked_at")
    worker = status.get("worker")
    if not isinstance(worker, dict):
        raise RuntimeError("swing worker lineage unavailable: data-status.worker missing")
    worker_sha = str(worker.get("source_commit_sha") or "").strip().lower()
    if not _valid_commit_sha(worker_sha):
        raise RuntimeError("swing liquidity lineage unavailable: worker commit SHA invalid")
    return phase, checked_at, checked_dt, worker_sha


def collect_snapshot_with_worker_lineage(
    base_url: str,
    api_key: str,
    bybit_base_url: str,
    *,
    collect: Callable[[str, str, str], dict[str, Any]] = collect_snapshot,
    fetch: Callable[[str, str | None, float], dict[str, Any]] = _get_json,
) -> dict[str, Any]:
    status_url = f"{base_url.rstrip('/')}/v1/data-status"
    pre_status = fetch(status_url, api_key, 20.0)
    snapshot = collect(base_url, api_key, bybit_base_url)
    version = fetch(f"{base_url.rstrip('/')}/version", None, 20.0)
    post_status = fetch(status_url, api_key, 20.0)

    api_sha = str(version.get("commit_sha") or "").strip().lower()
    if not _valid_commit_sha(api_sha):
        raise RuntimeError("swing liquidity lineage unavailable: API commit SHA invalid")

    scan_data_as_of = str(snapshot.get("scan_data_as_of") or "").strip()
    scan_dt = _parse_timestamp(scan_data_as_of, "collected scan timestamp")
    pre = _worker_observation(pre_status, "pre_collection")
    post = _worker_observation(post_status, "post_collection")

    exact_matches = [item for item in (pre, post) if item[1] == scan_data_as_of]
    if exact_matches:
        matched_worker_shas = {item[3] for item in exact_matches}
        if len(matched_worker_shas) != 1:
            raise RuntimeError("swing liquidity lineage mismatch: ambiguous worker commit for collected scan")
        worker_sha = next(iter(matched_worker_shas))
        phases = [item[0] for item in exact_matches]
        observation = "both" if len(phases) == 2 else phases[0]
    else:
        pre_sha, post_sha = pre[3], post[3]
        if pre_sha != post_sha:
            raise RuntimeError("swing liquidity lineage mismatch: worker commit changed across collection window")
        if pre[2] > scan_dt or scan_dt > post[2]:
            raise RuntimeError(
                "swing liquidity lineage mismatch: collected scan identity was outside the bracketing worker-status window"
            )
        worker_sha = pre_sha
        observation = "bounded_rotation"

    if api_sha != worker_sha:
        raise RuntimeError(
            "swing liquidity lineage mismatch: API and observed swing worker commits differ; "
            "refusing to persist an ambiguously labelled forward capture"
        )

    snapshot["source_commit_sha"] = api_sha
    snapshot["swing_worker_source_commit_sha"] = worker_sha
    snapshot["swing_worker_checked_at"] = scan_data_as_of
    snapshot["swing_worker_lineage_observation"] = observation
    snapshot["source_commit_semantics"] = "api_and_swing_worker_exact_commit_parity"
    for candidate in snapshot.get("candidates") or []:
        if isinstance(candidate, dict):
            candidate["swing_worker_source_commit_sha"] = worker_sha
            candidate["swing_worker_checked_at"] = scan_data_as_of
    return snapshot


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    bybit_base = os.getenv("BYBIT_EU_PUBLIC_BASE_URL", "https://api.bybit.eu").strip()
    output = Path(os.getenv("SWING_LIQUIDITY_SHADOW_OUTPUT", "swing-liquidity-shadow.json"))
    if not base_url or not api_key:
        raise SystemExit("required production API configuration is missing")
    return run_capture(
        base_url,
        api_key,
        bybit_base,
        output,
        collect=collect_snapshot_with_worker_lineage,
    )


if __name__ == "__main__":
    raise SystemExit(main())
