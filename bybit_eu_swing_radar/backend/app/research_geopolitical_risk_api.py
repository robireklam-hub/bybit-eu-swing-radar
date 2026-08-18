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
GDELT_DOC_HTTP_URL = "http://api.gdeltproject.org/api/v2/doc/doc"
GDELT_REQUEST_SPACING_SECONDS = 6.0
GDELT_MAX_RATE_LIMIT_RETRIES = 2
GDELT_RATE_LIMIT_BACKOFF_SECONDS = (12.0, 24.0)
GDELT_MAX_RETRY_AFTER_SECONDS = 60.0

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


def _status(
    status: str,
    *,
    bins: int = 0,
    reason: str | None = None,
    url: str = GDELT_DOC_URL,
    transport: str = "HTTPS",
    rate_limit_retries: int = 0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "provider": PROVIDER,
        "mode": "TimelineVolRaw",
        "lookback": "24h",
        "bins": int(bins),
        "url": url,
        "transport": transport,
        "transport_security": "TLS" if transport == "HTTPS" else "PLAINTEXT_PROVIDER_FALLBACK",
        "rate_limit_retries": int(rate_limit_retries),
        "request_spacing_seconds": GDELT_REQUEST_SPACING_SECONDS,
    }
    if reason:
        payload["reason"] = reason
    return payload


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    """Return a bounded provider-friendly delay for a 429 retry."""
    retry_after = response.headers.get("Retry-After")
    if retry_after not in (None, ""):
        try:
            seconds = float(retry_after)
        except (TypeError, ValueError):
            seconds = 0.0
        if seconds > 0:
            return min(seconds, GDELT_MAX_RETRY_AFTER_SECONDS)
    index = min(max(int(attempt), 0), len(GDELT_RATE_LIMIT_BACKOFF_SECONDS) - 1)
    return GDELT_RATE_LIMIT_BACKOFF_SECONDS[index]


async def _provider_request(
    client: httpx.AsyncClient,
    url: str,
    params: Mapping[str, str],
) -> tuple[dict[str, Any], int]:
    """Fetch one GDELT query with bounded 429-only retries.

    Other HTTP errors remain terminal. Connection errors are intentionally
    propagated so `_fetch_topic` can apply the separately documented official
    HTTP transport fallback only for connect-level failures.
    """
    for attempt in range(GDELT_MAX_RATE_LIMIT_RETRIES + 1):
        response = await client.get(url, params=params)
        if response.status_code == 429 and attempt < GDELT_MAX_RATE_LIMIT_RETRIES:
            await asyncio.sleep(_retry_delay(response, attempt))
            continue
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Provider returned non-object JSON")
        return payload, attempt
    raise RuntimeError("Unreachable GDELT retry state")


async def _fetch_topic(
    client: httpx.AsyncClient,
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
        payload, rate_limit_retries = await _provider_request(client, GDELT_DOC_URL, params)
        points = extract_timeline_points(payload)
        if not points:
            return name, payload, _status(
                "PARTIAL",
                reason="No valid TimelineVolRaw bins returned",
                rate_limit_retries=rate_limit_retries,
            )
        reason = (
            "Recovered after bounded GDELT 429 backoff"
            if rate_limit_retries > 0
            else None
        )
        return name, payload, _status(
            "LIVE",
            bins=len(points),
            reason=reason,
            rate_limit_retries=rate_limit_retries,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as https_exc:
        # GDELT officially supports DOC 2.0 over both HTTPS and HTTP. HTTP is
        # used only when the TLS endpoint cannot be connected to from the
        # runtime network, and the downgrade remains explicit in provenance.
        try:
            payload, rate_limit_retries = await _provider_request(
                client, GDELT_DOC_HTTP_URL, params
            )
            points = extract_timeline_points(payload)
            if not points:
                return name, payload, _status(
                    "PARTIAL",
                    reason=(
                        f"HTTPS {type(https_exc).__name__}; official HTTP fallback returned no valid TimelineVolRaw bins"
                    ),
                    url=GDELT_DOC_HTTP_URL,
                    transport="HTTP",
                    rate_limit_retries=rate_limit_retries,
                )
            return name, payload, _status(
                "PARTIAL",
                bins=len(points),
                reason=(
                    f"HTTPS {type(https_exc).__name__}; official HTTP transport fallback used"
                ),
                url=GDELT_DOC_HTTP_URL,
                transport="HTTP",
                rate_limit_retries=rate_limit_retries,
            )
        except Exception as http_exc:
            return name, {}, _status(
                "ERROR",
                reason=(
                    f"HTTPS {type(https_exc).__name__}; HTTP fallback {type(http_exc).__name__}: {str(http_exc)[:120]}"
                ),
            )
    except Exception as exc:
        return name, {}, _status(
            "ERROR", reason=f"{type(exc).__name__}: {str(exc)[:180]}"
        )


async def build_current_snapshot() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    timeout = httpx.Timeout(35.0, connect=10.0)
    limits = httpx.Limits(max_connections=2, max_keepalive_connections=1)
    results: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
        headers={"User-Agent": "bybit-eu-geopolitical-risk-shadow/1"},
    ) as client:
        # GDELT explicitly rate-limits its hosted APIs. Five fixed research
        # queries per hour are therefore deliberately serialized and paced,
        # rather than submitted as a burst via asyncio.gather.
        for index, (name, definition) in enumerate(TOPICS.items()):
            if index:
                await asyncio.sleep(GDELT_REQUEST_SPACING_SECONDS)
            results.append(await _fetch_topic(client, name, definition))

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
