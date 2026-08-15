#!/usr/bin/env python3
"""Dispatch v0.7.3 research backtest batches through the production Railway API.

The GitHub runner never connects to the production database and never calls
Bybit EU directly. Each batch is started on Railway, then this client polls the
existing read-only backtest status endpoint until progress advances.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ALLOWED_BATCH_COUNTS = {1, 5, 15}
POLL_SECONDS = 10
BATCH_TIMEOUT_SECONDS = 20 * 60


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


def _request_json(method: str, path: str) -> tuple[int, dict[str, Any]]:
    base_url = _required_env("PRODUCTION_RADAR_API_BASE_URL").rstrip("/")
    api_key = _required_env("PRODUCTION_RADAR_API_KEY")
    request = Request(
        f"{base_url}{path}",
        method=method,
        headers={
            "Accept": "application/json",
            "X-Radar-Key": api_key,
        },
    )
    try:
        with urlopen(request, timeout=45) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        payload: dict[str, Any]
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {"detail": body[:500]}
        if exc.code == 409:
            return exc.code, payload
        raise RuntimeError(
            f"Production API {method} {path} failed with HTTP {exc.code}: {payload}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"Production API {method} {path} network error: {exc.reason}"
        ) from exc


def _progress(payload: dict[str, Any]) -> tuple[bool, int, str, int]:
    exists = bool(payload.get("exists"))
    job = payload.get("job") or {}
    completed = int(job.get("completed_symbols") or 0)
    failed = int(job.get("failed_symbols") or 0)
    total = int(job.get("total_symbols") or 0)
    status = str(job.get("status") or "NOT_INITIALIZED")
    return exists, completed + failed, status, total


def _terminal(status: str) -> bool:
    return status in {"COMPLETED", "PARTIAL", "FAILED"}


def _batch_count() -> int:
    raw = os.getenv("BACKTEST_BATCH_COUNT", "1").strip()
    try:
        count = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid BACKTEST_BATCH_COUNT={raw!r}") from exc
    if count not in ALLOWED_BATCH_COUNTS:
        raise RuntimeError(f"Unsupported batch count: {count}")
    return count


def main() -> int:
    batch_count = _batch_count()
    for index in range(1, batch_count + 1):
        _, before = _request_json("GET", "/v1/day-trade/backtest/status")
        exists, baseline_done, status, total = _progress(before)
        print(
            f"batch {index}/{batch_count}: before exists={exists} "
            f"done={baseline_done}/{total} status={status}"
        )
        if exists and _terminal(status):
            print("Backtest job is already terminal; no further batch dispatch needed.")
            break

        code, dispatch = _request_json("POST", "/v1/day-trade/backtest/run-batch")
        if code == 409:
            print("A Railway backtest batch is already running; polling that batch.")
        else:
            print(f"Railway batch dispatch accepted: HTTP {code} {dispatch}")

        deadline = time.monotonic() + BATCH_TIMEOUT_SECONDS
        while True:
            time.sleep(POLL_SECONDS)
            _, current = _request_json("GET", "/v1/day-trade/backtest/status")
            current_exists, current_done, current_status, current_total = _progress(current)
            print(
                f"batch {index}/{batch_count}: poll exists={current_exists} "
                f"done={current_done}/{current_total} status={current_status}"
            )
            if current_exists and (
                current_done > baseline_done or _terminal(current_status)
            ):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Timed out waiting for Railway backtest batch {index} to advance"
                )

    _, final = _request_json("GET", "/v1/day-trade/backtest/status")
    print("FINAL_BACKTEST_STATUS=" + json.dumps(final, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
