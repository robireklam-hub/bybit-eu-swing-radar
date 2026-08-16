"""Research-only production API for premium-index microstructure v3."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import asyncpg
import httpx
from fastapi import Depends, FastAPI, HTTPException

from app.config import settings
from research_dataset_v1 import JOB_NAME, STRATEGY_VERSION
from research_historical_flow_fetch_v2 import choose_derivative_market
from research_premium_fetch_v3 import HistoricalPremiumAPI
from research_premium_microstructure_v3 import build_premium_report, enrich_with_premium

logger = logging.getLogger(__name__)
REPORT_CACHE_KEY = "day_trade_research_premium_v3_report"
STATUS_CACHE_KEY = "day_trade_research_premium_v3_status"
MAX_CONCURRENCY = 6
_task: asyncio.Task[None] | None = None


async def _connect() -> asyncpg.Connection:
    return await asyncpg.connect(settings.database_url)


async def _cache_put(conn: asyncpg.Connection, key: str, payload: dict[str, Any]) -> None:
    await conn.execute(
        """
        INSERT INTO radar_cache (cache_key,payload,updated_at)
        VALUES ($1,$2::jsonb,NOW())
        ON CONFLICT (cache_key)
        DO UPDATE SET payload=EXCLUDED.payload,updated_at=NOW()
        """,
        key,
        json.dumps(payload, default=str),
    )


async def _cache_get(conn: asyncpg.Connection, key: str) -> dict[str, Any] | None:
    raw = await conn.fetchval("SELECT payload FROM radar_cache WHERE cache_key=$1", key)
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


async def build_production_premium_report() -> dict[str, Any]:
    conn = await _connect()
    try:
        job_raw = await conn.fetchrow(
            """
            SELECT * FROM day_trade_diagnostic_jobs
            WHERE strategy_version=$1 AND job_name=$2
            ORDER BY id DESC LIMIT 1
            """,
            STRATEGY_VERSION,
            JOB_NAME,
        )
        if not job_raw:
            raise RuntimeError("research dataset v1 job not found")
        job = dict(job_raw)
        if str(job.get("status")) != "COMPLETED":
            raise RuntimeError(f"research dataset v1 must be COMPLETED, got {job.get('status')}")
        rows_raw = await conn.fetch(
            """
            SELECT dataset_split,symbol,side,opened_at,base_net_r,expansion_score
            FROM day_trade_diagnostic_events
            WHERE job_id=$1 AND candidate_built AND pass_structure_5m
              AND base_net_r IS NOT NULL
            ORDER BY opened_at,id
            """,
            int(job["id"]),
        )
        await _cache_put(
            conn,
            STATUS_CACHE_KEY,
            {
                "status": "RUNNING",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "dataset_job_id": int(job["id"]),
                "research_only": True,
            },
        )
    finally:
        await conn.close()

    rows = [dict(row) for row in rows_raw]
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_symbol.setdefault(str(row["symbol"]).upper(), []).append(row)

    timeout = httpx.Timeout(45.0, connect=15.0)
    limits = httpx.Limits(max_connections=MAX_CONCURRENCY * 2)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        api = HistoricalPremiumAPI(client)
        instruments, tickers = await asyncio.gather(api.linear_instruments(), api.linear_tickers())
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        start_ms = int((job["start_at"] - timedelta(hours=30)).timestamp() * 1000)
        end_ms = int(job["end_at"].timestamp() * 1000)

        async def process_symbol(spot_symbol: str, symbol_rows: list[dict[str, Any]]):
            base = spot_symbol.removesuffix("USDC")
            derivative_symbol = choose_derivative_market(base, instruments, tickers)
            if derivative_symbol is None:
                return [
                    enrich_with_premium(row, derivative_symbol=None, points=[])
                    for row in symbol_rows
                ], {"symbol": spot_symbol, "status": "NO_DERIVATIVE_MATCH"}
            async with semaphore:
                try:
                    points = await api.premium_history(
                        derivative_symbol, start_ms=start_ms, end_ms=end_ms
                    )
                except Exception as exc:
                    return [
                        enrich_with_premium(
                            row, derivative_symbol=derivative_symbol, points=[]
                        )
                        for row in symbol_rows
                    ], {
                        "symbol": spot_symbol,
                        "derivative_symbol": derivative_symbol,
                        "status": "ERROR",
                        "error": str(exc)[:500],
                    }
            enriched = [
                enrich_with_premium(
                    row, derivative_symbol=derivative_symbol, points=points
                )
                for row in symbol_rows
            ]
            return enriched, {
                "symbol": spot_symbol,
                "derivative_symbol": derivative_symbol,
                "status": "OK",
                "premium_points": len(points),
            }

        results = await asyncio.gather(
            *(process_symbol(symbol, symbol_rows) for symbol, symbol_rows in by_symbol.items())
        )

    enriched_rows: list[dict[str, Any]] = []
    symbol_status: list[dict[str, Any]] = []
    for symbol_rows, status in results:
        enriched_rows.extend(symbol_rows)
        symbol_status.append(status)

    analysis = build_premium_report(
        enriched_rows,
        start_at=job["start_at"],
        development_end_at=job["development_end_at"],
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_version": STRATEGY_VERSION,
        "dataset_job_id": int(job["id"]),
        "research_only": True,
        "live_strategy_mutated": False,
        "coverage": analysis["coverage"],
        "symbol_status": sorted(symbol_status, key=lambda item: item["symbol"]),
        "analysis": analysis,
    }

    conn = await _connect()
    try:
        await _cache_put(conn, REPORT_CACHE_KEY, report)
        await _cache_put(
            conn,
            STATUS_CACHE_KEY,
            {
                "status": "COMPLETED",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "dataset_job_id": int(job["id"]),
                "rows": len(enriched_rows),
                "coverage": analysis["coverage"],
                "research_only": True,
            },
        )
    finally:
        await conn.close()
    return report


async def _run_background() -> None:
    global _task
    try:
        await build_production_premium_report()
    except Exception as exc:
        logger.exception("premium microstructure v3 research failed")
        try:
            conn = await _connect()
            try:
                await _cache_put(
                    conn,
                    STATUS_CACHE_KEY,
                    {
                        "status": "FAILED",
                        "failed_at": datetime.now(timezone.utc).isoformat(),
                        "error": str(exc)[:1000],
                        "research_only": True,
                    },
                )
            finally:
                await conn.close()
        except Exception:
            logger.exception("failed to persist premium v3 failure status")
    finally:
        _task = None


def attach_v073_research_premium_v3_routes(
    app: FastAPI, require_api_key: Callable[..., Any]
) -> None:
    @app.post(
        "/v1/day-trade/research/premium/v3/run",
        status_code=202,
        dependencies=[Depends(require_api_key)],
    )
    async def run_premium_v3() -> dict[str, Any]:
        global _task
        if _task is not None and not _task.done():
            raise HTTPException(status_code=409, detail="premium v3 research already running")
        _task = asyncio.create_task(_run_background(), name="v073-premium-v3")
        return {
            "accepted": True,
            "research_only": True,
            "live_strategy_mutated": False,
            "execution": "railway_background_research",
        }

    @app.get(
        "/v1/day-trade/research/premium/v3/status",
        dependencies=[Depends(require_api_key)],
    )
    async def status_premium_v3() -> dict[str, Any]:
        conn = await _connect()
        try:
            payload = await _cache_get(conn, STATUS_CACHE_KEY)
        finally:
            await conn.close()
        return payload or {"status": "NOT_RUN", "research_only": True}

    @app.get(
        "/v1/day-trade/research/premium/v3/report",
        dependencies=[Depends(require_api_key)],
    )
    async def report_premium_v3() -> dict[str, Any]:
        conn = await _connect()
        try:
            payload = await _cache_get(conn, REPORT_CACHE_KEY)
        finally:
            await conn.close()
        if payload is None:
            raise HTTPException(status_code=404, detail="premium v3 report not found")
        return payload
