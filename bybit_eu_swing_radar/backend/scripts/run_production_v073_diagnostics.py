#!/usr/bin/env python3
"""Dispatch v0.7.3 gate-diagnostic batches through the production Railway API."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ALLOWED_BATCH_COUNTS = {1, 5, 15, 30}
POLL_SECONDS = 10
BATCH_TIMEOUT_SECONDS = 20 * 60


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


def _request_json(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    base_url = _required_env("PRODUCTION_RADAR_API_BASE_URL").rstrip("/")
    api_key = _required_env("PRODUCTION_RADAR_API_KEY")
    query = ("?" + urlencode(params)) if params else ""
    request = Request(
        f"{base_url}{path}{query}",
        method=method,
        headers={"Accept": "application/json", "X-Radar-Key": api_key},
    )
    try:
        with urlopen(request, timeout=45) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
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


def _batch_count() -> int:
    raw = os.getenv("DIAGNOSTIC_BATCH_COUNT", "1").strip()
    try:
        count = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid DIAGNOSTIC_BATCH_COUNT={raw!r}") from exc
    if count not in ALLOWED_BATCH_COUNTS:
        raise RuntimeError(f"Unsupported batch count: {count}")
    return count


def _progress(payload: dict[str, Any]) -> tuple[bool, int, int, int, str]:
    exists = bool(payload.get("exists"))
    job = payload.get("job") or {}
    completed = int(job.get("completed_symbols") or 0)
    failed = int(job.get("failed_symbols") or 0)
    total = int(job.get("total_symbols") or 0)
    status = str(job.get("status") or "NOT_INITIALIZED")
    return exists, completed, failed, total, status


def _runtime_status() -> dict[str, Any]:
    try:
        _, payload = _request_json(
            "GET", "/v1/day-trade/backtest/diagnostics/v073/runtime-status"
        )
        return payload.get("runtime_progress") or {}
    except Exception as exc:
        return {"stage": "RUNTIME_STATUS_UNAVAILABLE", "detail": {"error": str(exc)}}


def _terminal(status: str) -> bool:
    return status in {"COMPLETED", "PARTIAL", "FAILED"}


def main() -> int:
    count = _batch_count()
    status_path = "/v1/day-trade/backtest/diagnostics/v073/status"
    dispatch_path = "/v1/day-trade/backtest/diagnostics/v073/run-batch"

    for index in range(1, count + 1):
        _, before = _request_json("GET", status_path)
        exists, completed_before, failed_before, total, status = _progress(before)
        done_before = completed_before + failed_before
        runtime = _runtime_status()
        print(
            f"batch {index}/{count}: before exists={exists} done={done_before}/{total} "
            f"failed={failed_before} status={status} stage={runtime.get('stage')} "
            f"symbol={runtime.get('symbol')} heartbeat={runtime.get('heartbeat_at')}"
        )
        if exists and _terminal(status):
            if status != "COMPLETED" or failed_before:
                raise RuntimeError(
                    f"Diagnostic job is terminal but not clean: status={status}, failed={failed_before}"
                )
            print("Diagnostic job already completed; no further dispatch needed.")
            break

        code, dispatch = _request_json("POST", dispatch_path)
        if code == 409:
            print("A Railway diagnostic batch is already running; polling it.")
        else:
            print(f"Railway diagnostic batch accepted: HTTP {code} {dispatch}")

        deadline = time.monotonic() + BATCH_TIMEOUT_SECONDS
        while True:
            time.sleep(POLL_SECONDS)
            _, current = _request_json("GET", status_path)
            current_exists, completed, failed, current_total, current_status = _progress(current)
            done = completed + failed
            runtime = _runtime_status()
            print(
                f"batch {index}/{count}: poll exists={current_exists} done={done}/{current_total} "
                f"failed={failed} status={current_status} stage={runtime.get('stage')} "
                f"symbol={runtime.get('symbol')} heartbeat={runtime.get('heartbeat_at')} "
                f"detail={runtime.get('detail')}"
            )
            if failed > failed_before:
                raise RuntimeError(
                    f"Diagnostic batch recorded a failed symbol: failed {failed_before}->{failed}"
                )
            if current_exists and (done > done_before or _terminal(current_status)):
                if _terminal(current_status) and current_status != "COMPLETED":
                    raise RuntimeError(f"Diagnostic job ended with status={current_status}")
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Timed out waiting for diagnostic batch {index} to advance; runtime={runtime}"
                )

    _, final = _request_json("GET", status_path)
    print("FINAL_DIAGNOSTIC_STATUS=" + json.dumps(final, default=str, sort_keys=True))
    print("FINAL_RUNTIME_STATUS=" + json.dumps(_runtime_status(), default=str, sort_keys=True))

    for split in ("all", "DEVELOPMENT", "VALIDATION"):
        for side in ("both", "long", "short"):
            _, waterfall = _request_json(
                "GET",
                "/v1/day-trade/backtest/diagnostics/v073/waterfall",
                {"split": split, "side": side, "primary_only": "false"},
            )
            print(f"WATERFALL_{split}_{side}=" + json.dumps(waterfall, default=str, sort_keys=True))

    for split in ("all", "DEVELOPMENT", "VALIDATION"):
        for side in ("both", "long", "short"):
            _, edge = _request_json(
                "GET",
                "/v1/day-trade/backtest/diagnostics/v073/edge",
                {"split": split, "side": side, "primary_only": "true"},
            )
            print(f"EDGE_{split}_{side}=" + json.dumps(edge, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
