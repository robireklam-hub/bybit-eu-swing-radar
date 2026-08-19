"""Research-only ETH On-Chain Context v1 capture and persistence API."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import asyncpg
from research.research_snapshot_history import append_snapshot_history
import httpx
from fastapi import Depends, FastAPI, HTTPException

from research.eth_onchain_shadow import (
    COIN_METRICS,
    CORE_METRICS,
    OPTIONAL_METRICS,
    SPEC_VERSION,
    build_snapshot,
    spec,
    summarize_coin_metrics,
)

COIN_METRICS_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_eth_onchain_snapshots (
    spec_version TEXT NOT NULL,
    captured_hour TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (spec_version, captured_hour)
);
CREATE INDEX IF NOT EXISTS idx_research_eth_onchain_time
ON research_eth_onchain_snapshots(captured_at DESC);
"""


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is not configured")
    return value


def _decode(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _source_status(status: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, **extra}


async def _fetch_metric(
    client: httpx.AsyncClient,
    *,
    metric: str,
    today_utc: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch one metric independently so provider coverage failures stay isolated."""
    closed_through = today_utc - timedelta(days=1)
    start = closed_through - timedelta(days=105)
    response = await client.get(
        COIN_METRICS_URL,
        params={
            "assets": "eth",
            "metrics": metric,
            "frequency": "1d",
            "start_time": start.isoformat(),
            "end_time": closed_through.isoformat(),
            "page_size": 200,
            "paging_from": "start",
            "ignore_forbidden_errors": "true",
            "ignore_unsupported_errors": "true",
        },
        timeout=httpx.Timeout(20.0, connect=7.0),
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Coin Metrics response data is not a list")
    return rows, _source_status(
        "LIVE" if rows else "EMPTY",
        provider="Coin Metrics Community API",
        official_provider_api=True,
        keyless=True,
        asset="eth",
        metric=metric,
        frequency="1d",
        closed_daily_only=True,
        closed_through=closed_through.isoformat(),
        url=COIN_METRICS_URL,
    )


async def _safe_metric(
    client: httpx.AsyncClient,
    *,
    metric: str,
    today_utc: date,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    try:
        rows, status = await _fetch_metric(client, metric=metric, today_utc=today_utc)
        return metric, rows, status
    except Exception as exc:
        return metric, [], _source_status(
            "ERROR",
            provider="Coin Metrics Community API",
            metric=metric,
            reason=f"{type(exc).__name__}: {str(exc)[:180]}",
        )


def _merge_metric_rows(results: list[tuple[str, list[dict[str, Any]], dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for metric, rows, _ in results:
        for row in rows:
            if not isinstance(row, dict):
                continue
            timestamp = str(row.get("time") or "").strip()
            if not timestamp:
                continue
            target = merged.setdefault(timestamp, {"asset": "eth", "time": timestamp})
            if metric in row:
                target[metric] = row.get(metric)
    return sorted(merged.values(), key=lambda item: str(item.get("time") or ""))


async def build_current_snapshot(*, now: datetime | None = None) -> dict[str, Any]:
    captured_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    headers = {
        "User-Agent": "bybit-eu-eth-onchain-shadow/1",
        "Accept": "application/json,text/plain,*/*",
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        results = await asyncio.gather(
            *[
                _safe_metric(client, metric=metric, today_utc=captured_at.date())
                for metric in COIN_METRICS
            ]
        )

    closed_through = captured_at.date() - timedelta(days=1)
    summary, available = summarize_coin_metrics(
        _merge_metric_rows(results),
        closed_through=closed_through,
    )
    per_metric = {metric: status for metric, _, status in results}
    for metric in COIN_METRICS:
        if metric not in available and per_metric[metric].get("status") == "LIVE":
            per_metric[metric] = {**per_metric[metric], "status": "EMPTY"}

    core_available = [metric for metric in CORE_METRICS if metric in available]
    optional_available = [metric for metric in OPTIONAL_METRICS if metric in available]
    if len(core_available) == len(CORE_METRICS):
        aggregate_status = "LIVE"
    elif available:
        aggregate_status = "PARTIAL"
    else:
        aggregate_status = "ERROR"

    source_status = {
        "coin_metrics": _source_status(
            aggregate_status,
            provider="Coin Metrics Community API",
            official_provider_api=True,
            keyless=True,
            request_mode="per_metric_fail_transparent",
            core_requested_metrics=list(CORE_METRICS),
            core_available_metrics=core_available,
            core_missing_metrics=[metric for metric in CORE_METRICS if metric not in core_available],
            optional_requested_metrics=list(OPTIONAL_METRICS),
            optional_available_metrics=optional_available,
            optional_missing_metrics=[metric for metric in OPTIONAL_METRICS if metric not in optional_available],
            per_metric=per_metric,
            url=COIN_METRICS_URL,
        )
    }
    return build_snapshot(
        coin_metrics=summary,
        source_status=source_status,
        source_commit_sha=os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
        captured_at=captured_at,
    )


async def persist_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    captured_at = datetime.fromisoformat(str(snapshot["captured_at"]).replace("Z", "+00:00"))
    captured_hour = captured_at.replace(minute=0, second=0, microsecond=0)
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        await connection.execute(SCHEMA_SQL)
        history = await append_snapshot_history(
            connection,
            research_family="eth-onchain",
            spec_version=SPEC_VERSION,
            captured_at=captured_at,
            capture_bucket=captured_hour,
            source_commit_sha=snapshot.get("source_commit_sha"),
            snapshot=snapshot,
        )
        snapshot["immutable_history"] = history
        await connection.execute(
            """
            INSERT INTO research_eth_onchain_snapshots (
                spec_version,captured_hour,captured_at,source_commit_sha,payload,updated_at
            ) VALUES ($1,$2,$3,$4,$5::jsonb,NOW())
            ON CONFLICT (spec_version,captured_hour) DO UPDATE SET
                captured_at=EXCLUDED.captured_at,
                source_commit_sha=EXCLUDED.source_commit_sha,
                payload=EXCLUDED.payload,
                updated_at=NOW()
            """,
            SPEC_VERSION,
            captured_hour,
            captured_at,
            snapshot.get("source_commit_sha"),
            json.dumps(snapshot, separators=(",", ":")),
        )
    finally:
        await connection.close()
    return {**snapshot, "persisted": True, "captured_hour": captured_hour.isoformat()}


async def capture_current_snapshot() -> dict[str, Any]:
    return await persist_snapshot(await build_current_snapshot())


async def status_payload() -> dict[str, Any]:
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        await connection.execute(SCHEMA_SQL)
        latest = await connection.fetchrow(
            """
            SELECT captured_at,captured_hour,source_commit_sha,payload
            FROM research_eth_onchain_snapshots
            WHERE spec_version=$1
            ORDER BY captured_at DESC LIMIT 1
            """,
            SPEC_VERSION,
        )
        total = await connection.fetchval(
            "SELECT COUNT(*)::int FROM research_eth_onchain_snapshots WHERE spec_version=$1",
            SPEC_VERSION,
        )
    finally:
        await connection.close()

    latest_payload = None
    if latest:
        latest_payload = _decode(latest["payload"])
        latest_payload["captured_hour"] = latest["captured_hour"].isoformat()
        latest_payload["source_commit_sha"] = latest["source_commit_sha"]
    return {
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "execution_proof": False,
        "promotion_allowed": False,
        "spec": spec(),
        "snapshot_count": int(total or 0),
        "latest": latest_payload,
    }


def attach_eth_onchain_research(app: FastAPI, require_api_key: Callable[..., Any]) -> None:
    @app.get(
        "/v1/research/eth-onchain/spec",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def eth_onchain_spec() -> dict[str, Any]:
        return spec()

    @app.post(
        "/v1/research/eth-onchain/capture",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def eth_onchain_capture() -> dict[str, Any]:
        try:
            return await capture_current_snapshot()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research ETH on-chain capture unavailable: {type(exc).__name__}",
            ) from exc

    @app.get(
        "/v1/research/eth-onchain/status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def eth_onchain_status() -> dict[str, Any]:
        try:
            return await status_payload()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research ETH on-chain status unavailable: {type(exc).__name__}",
            ) from exc
