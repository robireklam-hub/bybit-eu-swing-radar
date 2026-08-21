#!/usr/bin/env python3
"""Fail-closed natural swing-liquidity capture with exact API/worker lineage parity.

Research only. The durable capture table records the production API commit SHA as
``source_commit_sha``. During rolling deploys the API and swing worker can briefly
serve different commits, which would mislabel forward research lineage.

The scan cache can rotate while order books are being collected. A pre/post-only
status bracket is not sufficient because the collected scan can exist strictly
between those two observations. This wrapper therefore samples worker status at a
small bounded cadence while collection is in flight and accepts the snapshot only
when its exact ``data_as_of`` identity was actually observed, and the observed
worker commit equals the API commit. No live score, eligibility, threshold, or
execution state is changed.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from research.swing_liquidity_shadow import collect_snapshot  # noqa: E402
from scripts.run_swing_liquidity_shadow_guarded import run_capture  # noqa: E402


STATUS_POLL_INTERVAL_SECONDS = 0.5


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


def _matching_worker_observation(
    status: dict[str, Any],
    scan_data_as_of: str,
    phase: str,
) -> tuple[str, str, str] | None:
    checked_at = str(status.get("checked_at") or "").strip()
    if checked_at != scan_data_as_of:
        return None
    worker = status.get("worker")
    if not isinstance(worker, dict):
        raise RuntimeError("swing worker lineage unavailable: data-status.worker missing")
    worker_sha = str(worker.get("source_commit_sha") or "").strip().lower()
    if not _valid_commit_sha(worker_sha):
        raise RuntimeError("swing liquidity lineage unavailable: worker commit SHA invalid")
    return phase, checked_at, worker_sha


def _matching_observations(
    observations: list[tuple[str, dict[str, Any]]],
    scan_data_as_of: str,
) -> list[tuple[str, str, str]]:
    return [
        match
        for phase, status in observations
        if (match := _matching_worker_observation(status, scan_data_as_of, phase)) is not None
    ]


def _observation_label(phases: list[str]) -> str:
    unique = set(phases)
    if unique == {"pre_collection", "post_collection"}:
        return "both"
    if "during_collection" in unique:
        return "during_collection"
    if len(unique) == 1:
        return phases[0]
    return "bracketed"


def collect_snapshot_with_worker_lineage(
    base_url: str,
    api_key: str,
    bybit_base_url: str,
    *,
    collect: Callable[[str, str, str], dict[str, Any]] = collect_snapshot,
    fetch: Callable[[str, str | None, float], dict[str, Any]] = _get_json,
    poll_interval_seconds: float = STATUS_POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    status_url = f"{base_url.rstrip('/')}/v1/data-status"
    observations: list[tuple[str, dict[str, Any]]] = [
        ("pre_collection", fetch(status_url, api_key, 20.0))
    ]
    observation_lock = threading.Lock()
    stop_polling = threading.Event()

    def poll_worker_status() -> None:
        while not stop_polling.wait(max(0.01, poll_interval_seconds)):
            try:
                status = fetch(status_url, api_key, 20.0)
            except Exception:
                continue
            with observation_lock:
                observations.append(("during_collection", status))

    poller = threading.Thread(target=poll_worker_status, name="swing-lineage-status-poller", daemon=True)
    poller.start()
    try:
        snapshot = collect(base_url, api_key, bybit_base_url)
    finally:
        stop_polling.set()
        poller.join(timeout=max(1.0, poll_interval_seconds * 4.0))

    version = fetch(f"{base_url.rstrip('/')}/version", None, 20.0)
    with observation_lock:
        observations.append(("post_collection", fetch(status_url, api_key, 20.0)))
        frozen_observations = list(observations)

    api_sha = str(version.get("commit_sha") or "").strip().lower()
    if not _valid_commit_sha(api_sha):
        raise RuntimeError("swing liquidity lineage unavailable: API commit SHA invalid")

    scan_data_as_of = str(snapshot.get("scan_data_as_of") or "").strip()
    if not scan_data_as_of:
        raise RuntimeError("swing liquidity lineage unavailable: collected scan timestamp missing")

    matches = _matching_observations(frozen_observations, scan_data_as_of)
    if not matches:
        raise RuntimeError(
            "swing liquidity lineage mismatch: collected scan identity was not observed in "
            "the bounded worker-status observations"
        )

    matched_worker_shas = {item[2] for item in matches}
    if len(matched_worker_shas) != 1:
        raise RuntimeError("swing liquidity lineage mismatch: ambiguous worker commit for collected scan")
    worker_sha = next(iter(matched_worker_shas))
    if api_sha != worker_sha:
        raise RuntimeError(
            "swing liquidity lineage mismatch: API and observed swing worker commits differ; "
            "refusing to persist an ambiguously labelled forward capture"
        )

    phases = [item[0] for item in matches]
    worker_checked_at = scan_data_as_of
    snapshot["source_commit_sha"] = api_sha
    snapshot["swing_worker_source_commit_sha"] = worker_sha
    snapshot["swing_worker_checked_at"] = worker_checked_at
    snapshot["swing_worker_lineage_observation"] = _observation_label(phases)
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
