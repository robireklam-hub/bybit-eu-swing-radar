"""Research-only BTC On-Chain Context v1 capture and persistence API."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import asyncpg
import httpx
from fastapi import Depends, FastAPI, HTTPException

from research.btc_onchain_shadow import (
    COIN_METRICS,
    SPEC_VERSION,
    build_snapshot,
    compact_difficulty,
    compact_fees,
    compact_mempool,
    spec,
    summarize_coin_metrics,
)

COIN_METRICS_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
MEMPOOL_BASE_URL = "https://mempool.space"
MEMPOOL_URL = f"{MEMPOOL_BASE_URL}/api/mempool"
FEES_URL = f"{MEMPOOL_BASE_URL}/api/v1/fees/recommended"
DIFFICULTY_URL = f"{MEMPOOL_BASE_URL}/api/v1/difficulty-adjustment"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_btc_onchain_snapshots (
    spec_version TEXT NOT NULL,
    captured_hour TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (spec_version, captured_hour)
);
CREATE INDEX IF NOT EXISTS idx_research_btc_onchain_time
ON research_btc_onchain_snapshots(captured_at DESC);
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


async def _fetch_coin_metrics(
    client: httpx.AsyncClient, *, today_utc: date
) -> tuple[dict[str, Any], dict[str, Any]]:
    closed_through = today_utc - timedelta(days=1)
    start = closed_through - timedelta(days=105)
    response = await client.get(
        COIN_METRICS_URL,
        params={
            "assets": "btc",
            "metrics": ",".join(COIN_METRICS),
            "frequency": "1d",
            "start_time": start.isoformat(),
            "end_time": closed_through.isoformat(),
            "page_size": 200,
            "paging_from": "start",
        },
        timeout=httpx.Timeout(20.0, connect=7.0),
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Coin Metrics response data is not a list")
    summary, available = summarize_coin_metrics(rows, closed_through=closed_through)
    missing = [metric for metric in COIN_METRICS if metric not in available]
    status = "LIVE" if not missing else ("PARTIAL" if available else "ERROR")
    return summary, _source_status(
        status,
        provider="Coin Metrics Community API",
        official_provider_api=True,
        keyless=True,
        frequency="1d",
        closed_daily_only=True,
        closed_through=closed_through.isoformat(),
        requested_metrics=list(COIN_METRICS),
        available_metrics=available,
        missing_metrics=missing,
        url=COIN_METRICS_URL,
    )


async def _fetch_json(
    client: httpx.AsyncClient,
    *,
    name: str,
    url: str,
    compact: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    response = await client.get(url, timeout=httpx.Timeout(12.0, connect=5.0))
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"{name} response is not an object")
    return compact(payload), _source_status(
        "LIVE",
        provider="mempool.space",
        keyless=True,
        endpoint=name,
        url=url,
    )


async def _safe(name: str, awaitable: Any) -> tuple[str, Any, dict[str, Any]]:
    try:
        value, status = await awaitable
        return name, value, status
    except Exception as exc:
        return name, None, _source_status(
            "ERROR",
            reason=f"{type(exc).__name__}: {str(exc)[:180]}",
        )


async def build_current_snapshot(*, now: datetime | None = None) -> dict[str, Any]:
    captured_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    headers = {
        "User-Agent": "bybit-eu-btc-onchain-shadow/1",
        "Accept": "application/json,text/plain,*/*",
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        results = await asyncio.gather(
            _safe("coin_metrics", _fetch_coin_metrics(client, today_utc=captured_at.date())),
            _safe(
                "mempool",
                _fetch_json(client, name="mempool", url=MEMPOOL_URL, compact=compact_mempool),
            ),
            _safe(
                "recommended_fees",
                _fetch_json(client, name="recommended_fees", url=FEES_URL, compact=compact_fees),
            ),
            _safe(
                "difficulty_adjustment",
                _fetch_json(client, name="difficulty_adjustment", url=DIFFICULTY_URL, compact=compact_difficulty),
            ),
        )
    by_name = {name: (value, status) for name, value, status in results}
    return build_snapshot(
        coin_metrics=by_name["coin_metrics"][0],
        mempool=by_name["mempool"][0],
        recommended_fees=by_name["recommended_fees"][0],
        difficulty=by_name["difficulty_adjustment"][0],
        source_status={name: status for name, (_, status) in by_name.items()},
        source_commit_sha=os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
        captured_at=captured_at,
    )


async def persist_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    captured_at = datetime.fromisoformat(str(snapshot["captured_at"]).replace("Z", "+00:00"))
    captured_hour = captured_at.replace(minute=0, second=0, microsecond=0)
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        await connection.execute(SCHEMA_SQL)
        await connection.execute(
            """
            INSERT INTO research_btc_onchain_snapshots (
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
            FROM research_btc_onchain_snapshots
            WHERE spec_version=$1
            ORDER BY captured_at DESC LIMIT 1
            """,
            SPEC_VERSION,
        )
        total = await connection.fetchval(
            "SELECT COUNT(*)::int FROM research_btc_onchain_snapshots WHERE spec_version=$1",
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


def attach_btc_onchain_research(app: FastAPI, require_api_key: Callable[..., Any]) -> None:
    @app.get(
        "/v1/research/btc-onchain/spec",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def btc_onchain_spec() -> dict[str, Any]:
        return spec()

    @app.post(
        "/v1/research/btc-onchain/capture",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def btc_onchain_capture() -> dict[str, Any]:
        try:
            return await capture_current_snapshot()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research BTC on-chain capture unavailable: {type(exc).__name__}",
            ) from exc

    @app.get(
        "/v1/research/btc-onchain/status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def btc_onchain_status() -> dict[str, Any]:
        try:
            return await status_payload()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research BTC on-chain status unavailable: {type(exc).__name__}",
            ) from exc
