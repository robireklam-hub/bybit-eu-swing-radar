"""Production research-only API for breakout continuation strategy-family v5."""
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
from backtest import HistoricalBybitAPI
from research_breakout_continuation_v5 import build_breakout_report, replay_symbol_breakouts
from research_dataset_v1 import JOB_NAME, STRATEGY_VERSION

logger = logging.getLogger(__name__)
REPORT_CACHE_KEY = "day_trade_research_breakout_v5_report"
STATUS_CACHE_KEY = "day_trade_research_breakout_v5_status"
MAX_CONCURRENCY = 4
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


async def build_production_breakout_report() -> dict[str, Any]:
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
        symbol_rows = await conn.fetch(
            "SELECT DISTINCT symbol FROM day_trade_diagnostic_events WHERE job_id=$1 ORDER BY symbol",
            int(job["id"]),
        )
        symbols = [str(row["symbol"]).upper() for row in symbol_rows]
        await _cache_put(
            conn,
            STATUS_CACHE_KEY,
            {
                "status": "RUNNING",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "dataset_job_id": int(job["id"]),
                "total_symbols": len(symbols),
                "research_only": True,
            },
        )
    finally:
        await conn.close()

    timeout = httpx.Timeout(45.0, connect=15.0)
    limits = httpx.Limits(max_connections=MAX_CONCURRENCY * 2)
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    start_ms = int(job["start_at"].timestamp() * 1000)
    end_fetch_ms = int((job["end_at"] + timedelta(hours=8)).timestamp() * 1000)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        api = HistoricalBybitAPI(client)

        async def process_symbol(symbol: str):
            async with semaphore:
                try:
                    bars = await api.klines_range(symbol, start_ms, end_fetch_ms)
                except Exception as exc:
                    return [], {"symbol": symbol, "status": "ERROR", "error": str(exc)[:500]}
            events = replay_symbol_breakouts(
                symbol=symbol,
                bars=bars,
                start_at=job["start_at"],
                end_at=job["end_at"],
                development_end_at=job["development_end_at"],
            )
            return events, {"symbol": symbol, "status": "OK", "bars": len(bars), "events": len(events)}

        results = await asyncio.gather(*(process_symbol(symbol) for symbol in symbols))

    events: list[dict[str, Any]] = []
    symbol_status: list[dict[str, Any]] = []
    for symbol_events, status in results:
        events.extend(symbol_events)
        symbol_status.append(status)

    analysis = build_breakout_report(
        events,
        start_at=job["start_at"],
        development_end_at=job["development_end_at"],
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_version": STRATEGY_VERSION,
        "dataset_job_id": int(job["id"]),
        "events": len(events),
        "research_only": True,
        "live_strategy_mutated": False,
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
                "events": len(events),
                "winner": analysis.get("selected_on_train"),
                "holdout_edge_pass": analysis.get("internal_holdout_edge_pass"),
                "research_only": True,
            },
        )
    finally:
        await conn.close()
    return report


async def _run_background() -> None:
    global _task
    try:
        await build_production_breakout_report()
    except Exception as exc:
        logger.exception("breakout continuation v5 research failed")
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
            logger.exception("failed to persist breakout v5 failure status")
    finally:
        _task = None


def attach_v073_research_breakout_v5_routes(app: FastAPI, require_api_key: Callable[..., Any]) -> None:
    @app.post(
        "/v1/day-trade/research/breakout/v5/run",
        status_code=202,
        dependencies=[Depends(require_api_key)],
    )
    async def run_breakout_v5() -> dict[str, Any]:
        global _task
        if _task is not None and not _task.done():
            raise HTTPException(status_code=409, detail="breakout v5 research already running")
        _task = asyncio.create_task(_run_background(), name="v073-breakout-v5")
        return {
            "accepted": True,
            "research_only": True,
            "live_strategy_mutated": False,
            "execution": "railway_background_research",
        }

    @app.get(
        "/v1/day-trade/research/breakout/v5/status",
        dependencies=[Depends(require_api_key)],
    )
    async def status_breakout_v5() -> dict[str, Any]:
        conn = await _connect()
        try:
            payload = await _cache_get(conn, STATUS_CACHE_KEY)
        finally:
            await conn.close()
        return payload or {"status": "NOT_RUN", "research_only": True}

    @app.get(
        "/v1/day-trade/research/breakout/v5/report",
        dependencies=[Depends(require_api_key)],
    )
    async def report_breakout_v5() -> dict[str, Any]:
        conn = await _connect()
        try:
            payload = await _cache_get(conn, REPORT_CACHE_KEY)
        finally:
            await conn.close()
        if payload is None:
            raise HTTPException(status_code=404, detail="breakout v5 report not found")
        return payload
