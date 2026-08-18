"""Research-only sourced sector taxonomy / rotation v1 capture API."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable

import asyncpg
import httpx
from fastapi import Depends, FastAPI, HTTPException

from app.research_relative_strength_api import (
    build_current_snapshot as build_relative_strength_snapshot,
)
from research.sector_rotation_shadow import (
    SPEC_VERSION,
    build_functional_tag_index,
    build_snapshot,
    resolve_symbols,
    spec,
)

COINPAPRIKA_BASE_URL = "https://api.coinpaprika.com/v1"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_sector_rotation_snapshots (
    spec_version TEXT NOT NULL,
    captured_day DATE NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    universe_size INTEGER NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (spec_version, captured_day)
);
CREATE INDEX IF NOT EXISTS idx_research_sector_rotation_time
ON research_sector_rotation_snapshots(captured_at DESC);
"""


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is not configured")
    return value


def _source_status(status: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, **extra}


async def _fetch_tickers(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await client.get(
        f"{COINPAPRIKA_BASE_URL}/tickers",
        timeout=httpx.Timeout(25.0, connect=8.0),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("CoinPaprika tickers response is not a list")
    return [item for item in payload if isinstance(item, dict)]


async def _fetch_tags(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await client.get(
        f"{COINPAPRIKA_BASE_URL}/tags",
        params={"additional_fields": "coins"},
        timeout=httpx.Timeout(25.0, connect=8.0),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("CoinPaprika tags response is not a list")
    return [item for item in payload if isinstance(item, dict)]


async def _safe_provider(
    name: str, coroutine: Any
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    try:
        payload = await coroutine
        return name, payload, _source_status(
            "LIVE",
            provider="CoinPaprika",
            base_url=COINPAPRIKA_BASE_URL,
            item_count=len(payload),
            authentication="keyless_free_endpoint",
        )
    except Exception as exc:
        return name, [], _source_status(
            "ERROR",
            provider="CoinPaprika",
            base_url=COINPAPRIKA_BASE_URL,
            reason=f"{type(exc).__name__}: {str(exc)[:180]}",
        )


async def build_current_snapshot() -> dict[str, Any]:
    captured_at = datetime.now(timezone.utc)
    headers = {
        "User-Agent": "bybit-eu-sector-rotation-shadow/1",
        "Accept": "application/json,text/plain,*/*",
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        relative_task = build_relative_strength_snapshot()
        ticker_task = _safe_provider("tickers", _fetch_tickers(client))
        tag_task = _safe_provider("tags", _fetch_tags(client))
        relative_snapshot, ticker_result, tag_result = await asyncio.gather(
            relative_task, ticker_task, tag_task
        )

    provider_results = {
        ticker_result[0]: (ticker_result[1], ticker_result[2]),
        tag_result[0]: (tag_result[1], tag_result[2]),
    }
    tickers, ticker_status = provider_results["tickers"]
    tags, tag_status = provider_results["tags"]
    symbols = [
        str(item.get("symbol") or "").upper()
        for item in list(relative_snapshot.get("symbols") or [])
        if isinstance(item, dict) and item.get("symbol")
    ]
    resolutions = resolve_symbols(symbols, tickers)
    coin_tags, tag_meta = build_functional_tag_index(tags)
    snapshot = build_snapshot(
        relative_strength_snapshot=relative_snapshot,
        resolutions=resolutions,
        coin_tags=coin_tags,
        tag_meta=tag_meta,
        captured_at=captured_at,
    )
    source_status = {
        "relative_strength": _source_status(
            "LIVE",
            spec_version=(relative_snapshot.get("spec") or {}).get("version"),
            captured_at=relative_snapshot.get("captured_at"),
            source="Bybit EU USDC spot completed 1D OHLCV",
        ),
        "coinpaprika_tickers": ticker_status,
        "coinpaprika_tags": tag_status,
    }
    live_count = sum(1 for item in source_status.values() if item.get("status") == "LIVE")
    snapshot["source_status"] = source_status
    snapshot["data_quality"] = (
        "COMPLETE" if live_count == len(source_status) else "PARTIAL" if live_count else "MISSING"
    )
    snapshot["source_commit_sha"] = os.getenv("RAILWAY_GIT_COMMIT_SHA") or None
    return snapshot


async def persist_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    captured_at = datetime.fromisoformat(
        str(snapshot["captured_at"]).replace("Z", "+00:00")
    )
    captured_day = captured_at.astimezone(timezone.utc).date()
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        await connection.execute(SCHEMA_SQL)
        await connection.execute(
            """
            INSERT INTO research_sector_rotation_snapshots (
                spec_version,captured_day,captured_at,source_commit_sha,
                universe_size,payload,updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6::jsonb,NOW())
            ON CONFLICT (spec_version,captured_day) DO UPDATE SET
                captured_at=EXCLUDED.captured_at,
                source_commit_sha=EXCLUDED.source_commit_sha,
                universe_size=EXCLUDED.universe_size,
                payload=EXCLUDED.payload,
                updated_at=NOW()
            """,
            SPEC_VERSION,
            captured_day,
            captured_at,
            snapshot.get("source_commit_sha"),
            int(snapshot.get("universe_size") or 0),
            json.dumps(snapshot, separators=(",", ":")),
        )
    finally:
        await connection.close()
    return {**snapshot, "persisted": True, "captured_day": captured_day.isoformat()}


async def capture_current_snapshot() -> dict[str, Any]:
    return await persist_snapshot(await build_current_snapshot())


def _decode(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    return {}


async def status_payload() -> dict[str, Any]:
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        await connection.execute(SCHEMA_SQL)
        latest = await connection.fetchrow(
            """
            SELECT captured_day,captured_at,source_commit_sha,universe_size,payload
            FROM research_sector_rotation_snapshots
            WHERE spec_version=$1
            ORDER BY captured_at DESC LIMIT 1
            """,
            SPEC_VERSION,
        )
        total = await connection.fetchval(
            "SELECT COUNT(*)::int FROM research_sector_rotation_snapshots WHERE spec_version=$1",
            SPEC_VERSION,
        )
    finally:
        await connection.close()

    latest_payload = None
    if latest:
        latest_payload = _decode(latest["payload"])
        latest_payload["captured_day"] = latest["captured_day"].isoformat()
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


def attach_sector_rotation_research(
    app: FastAPI, require_api_key: Callable[..., Any]
) -> None:
    @app.get(
        "/v1/research/sector-rotation/spec",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def sector_rotation_spec() -> dict[str, Any]:
        return spec()

    @app.post(
        "/v1/research/sector-rotation/capture",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def sector_rotation_capture() -> dict[str, Any]:
        try:
            return await capture_current_snapshot()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research sector-rotation capture unavailable: {type(exc).__name__}",
            ) from exc

    @app.get(
        "/v1/research/sector-rotation/status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def sector_rotation_status() -> dict[str, Any]:
        try:
            return await status_payload()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research sector-rotation status unavailable: {type(exc).__name__}",
            ) from exc
