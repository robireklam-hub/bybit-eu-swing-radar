#!/usr/bin/env python3
"""Fail-closed natural swing-liquidity capture with exact API/worker lineage parity.

Research only. The durable capture table records the production API commit SHA as
``source_commit_sha``. During rolling deploys the API and swing worker can briefly
serve different commits, which would mislabel the worker-generated scan lineage.
This wrapper refuses to persist a capture unless the API build SHA equals the
worker SHA and the worker status timestamp exactly matches the scan timestamp that
was collected. No live score, eligibility, threshold, or execution state is
changed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from research.swing_liquidity_shadow import collect_snapshot  # noqa: E402
from scripts.run_swing_liquidity_shadow_guarded import run_capture  # noqa: E402


def _get_json(url: str, api_key: str | None = None, timeout: float = 20.0) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "swing-liquidity-worker-lineage/1"}
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


def collect_snapshot_with_worker_lineage(
    base_url: str,
    api_key: str,
    bybit_base_url: str,
    *,
    collect: Callable[[str, str, str], dict[str, Any]] = collect_snapshot,
    fetch: Callable[[str, str | None, float], dict[str, Any]] = _get_json,
) -> dict[str, Any]:
    snapshot = collect(base_url, api_key, bybit_base_url)
    version = fetch(f"{base_url.rstrip('/')}/version", None, 20.0)
    status = fetch(f"{base_url.rstrip('/')}/v1/data-status", api_key, 20.0)

    worker = status.get("worker")
    if not isinstance(worker, dict):
        raise RuntimeError("swing worker lineage unavailable: data-status.worker missing")

    api_sha = str(version.get("commit_sha") or "").strip().lower()
    worker_sha = str(worker.get("source_commit_sha") or "").strip().lower()
    if not _valid_commit_sha(api_sha):
        raise RuntimeError("swing liquidity lineage unavailable: API commit SHA invalid")
    if not _valid_commit_sha(worker_sha):
        raise RuntimeError("swing liquidity lineage unavailable: worker commit SHA invalid")
    if api_sha != worker_sha:
        raise RuntimeError(
            "swing liquidity lineage mismatch: API and swing worker commits differ; "
            "refusing to persist an ambiguously labelled forward capture"
        )

    scan_data_as_of = str(snapshot.get("scan_data_as_of") or "").strip()
    worker_checked_at = str(status.get("checked_at") or "").strip()
    if not scan_data_as_of or worker_checked_at != scan_data_as_of:
        raise RuntimeError(
            "swing liquidity lineage mismatch: data-status timestamp does not match collected scan"
        )

    snapshot["source_commit_sha"] = api_sha
    snapshot["swing_worker_source_commit_sha"] = worker_sha
    snapshot["swing_worker_checked_at"] = worker_checked_at
    snapshot["source_commit_semantics"] = "api_and_swing_worker_exact_commit_parity"
    for candidate in snapshot.get("candidates") or []:
        if isinstance(candidate, dict):
            candidate["swing_worker_source_commit_sha"] = worker_sha
            candidate["swing_worker_checked_at"] = worker_checked_at
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
