"""Research-only geopolitical news-attention capture/status API."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import asyncpg
import httpx
from fastapi import Depends, FastAPI, HTTPException

from research.geopolitical_risk_shadow import (
    PROVIDER,
    SPEC_VERSION,
    TOPICS,
    build_snapshot,
    extract_timeline_points,
    spec,
)

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_geopolitical_risk_snapshots (
    spec_version TEXT NOT NULL,
    captured_hour TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    data_quality TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (spec_version, captured_hour)
);
CREATE INDEX IF NOT EXISTS idx_research_geopolitical_risk_snapshot_time
ON research_geopolitical_risk_snapshots(captured_at DESC);
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


def _status(status: str, *, bins: int = 0, reason: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "provider": PROVIDER,
        "mode": "TimelineVolRaw",
        "lookback": "24h",
        "bins": int(bins),
        "url": GDELT_DOC_URL,
    }
    if reason:
        payload["reason"] = reason
    return payload


async def _fetch_topic(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    name: str,
    definition: Mapping[str, str],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    params = {
        "query": definition["query"],
        "mode": "TimelineVolRaw",
        "format": "json",
        "timespan": "24h",
        "timelinesmooth": "0",
    }
    try:
        async with semaphore:
            response = await client.get(GDELT_DOC_URL, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return name, {}, _status("ERROR", reason="Provider returned non-object JSON")
        points = extract_timeline_points(payload)
        if not points:
            return name, payload, _status("PARTIAL", reason="No valid TimelineVolRaw bins returned")
        return name, payload, _status("LIVE", bins=len(points))
    except Exception as exc:
        return name, {}, _status("ERROR", reason=f"{type(exc).__name__}: {str(exc)[:180]}")


async def build_current_snapshot() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    timeout = httpx.Timeout(30.0, connect=10.0)
    limits = httpx.Limits(max_connections=3, max_keepalive_connections=2)
    semaphore = asyncio.Semaphore(2)
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        headers={"User-Agent": "bybit-eu-geopolitical-risk-shadow/1"},
    ) as client:
        results = await asyncio.gather(
            *(
                _fetch_topic(client, semaphore, name, definition)
                for name, definition in TOPICS.items()
            )
        )

    topic_payloads: dict[str, dict[str, Any]] = {}
    source_status: dict[str, dict[str, Any]] = {}
    for name, payload, status in results:
        topic_payloads[name] = payload
        source_status[name] = status

    return build_snapshot(
        topic_payloads,
        source_status,
        captured_at=now,
        source_commit_sha=os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
    )


async def persist_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    captured_at = datetime.fromisoformat(str(snapshot["captured_at"]).replace("Z", "+00:00"))
    captured_hour = captured_at.replace(minute=0, second=0, microsecond=0)
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        await connection.execute(SCHEMA_SQL)
        await connection.execute(
            """
            INSERT INTO research_geopolitical_risk_snapshots (
                spec_version,captured_hour,captured_at,source_commit_sha,data_quality,payload,updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6::jsonb,NOW())
            ON CONFLICT (spec_version,captured_hour) DO UPDATE SET
                captured_at=EXCLUDED.captured_at,
                source_commit_sha=EXCLUDED.source_commit_sha,
                data_quality=EXCLUDED.data_quality,
                payload=EXCLUDED.payload,
                updated_at=NOW()
            """,
            SPEC_VERSION,
            captured_hour,
            captured_at,
            snapshot.get("source_commit_sha"),
            str(snapshot.get("data_quality") or "DEGRADED"),
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
            """SELECT captured_hour,source_commit_sha,data_quality,payload
               FROM research_geopolitical_risk_snapshots
               WHERE spec_version=$1 ORDER BY captured_at DESC LIMIT 1""",
            SPEC_VERSION,
        )
        snapshot_count = await connection.fetchval(
            "SELECT COUNT(*)::int FROM research_geopolitical_risk_snapshots WHERE spec_version=$1",
            SPEC_VERSION,
        )
    finally:
        await connection.close()

    latest_payload = None
    if latest:
        latest_payload = _decode(latest["payload"])
        latest_payload["captured_hour"] = latest["captured_hour"].isoformat()
        latest_payload["source_commit_sha"] = latest["source_commit_sha"]
        latest_payload["data_quality"] = latest["data_quality"]
    return {
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "promotion_allowed": False,
        "live_strategy_mutated": False,
        "spec": spec(),
        "snapshot_count": int(snapshot_count or 0),
        "latest": latest_payload,
    }


def attach_geopolitical_risk_research(app: FastAPI, require_api_key: Callable[..., Any]) -> None:
    @app.get(
        "/v1/research/geopolitical-risk/spec",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def geopolitical_risk_spec() -> dict[str, Any]:
        return spec()

    @app.post(
        "/v1/research/geopolitical-risk/capture",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def geopolitical_risk_capture() -> dict[str, Any]:
        try:
            return await capture_current_snapshot()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research geopolitical-risk capture unavailable: {type(exc).__name__}",
            ) from exc

    @app.get(
        "/v1/research/geopolitical-risk/status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def geopolitical_risk_status() -> dict[str, Any]:
        try:
            return await status_payload()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research geopolitical-risk status unavailable: {type(exc).__name__}",
            ) from exc
