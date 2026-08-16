"""Research-only production API for entry-architecture retest pivot v4."""
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
from research_dataset_v1 import JOB_NAME, STRATEGY_VERSION
from research_entry_retest_v4 import build_entry_retest_report, replay_entry_variant

logger = logging.getLogger(__name__)
REPORT_CACHE_KEY = "day_trade_research_entry_v4_report"
STATUS_CACHE_KEY = "day_trade_research_entry_v4_status"
MAX_CONCURRENCY = 4
VARIANTS = ("structure_break_retest", "half_retrace_to_break", "sweep_level_retest")
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


async def build_production_entry_report() -> dict[str, Any]:
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
            SELECT dataset_split,symbol,side,opened_at,base_net_r,
                   entry_price,stop_price,candidate_payload
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
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    start_ms = int(job["start_at"].timestamp() * 1000)
    end_ms = int((job["end_at"] + timedelta(hours=8)).timestamp() * 1000)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        api = HistoricalBybitAPI(client)

        async def process_symbol(symbol: str, symbol_rows: list[dict[str, Any]]):
            async with semaphore:
                try:
                    bars = await api.klines_range(symbol, start_ms, end_ms)
                except Exception as exc:
                    return symbol_rows, {"symbol": symbol, "status": "ERROR", "error": str(exc)[:500]}
            starts = [int(bar.start_ms) for bar in bars]
            enriched: list[dict[str, Any]] = []
            for row in symbol_rows:
                retests: dict[str, Any] = {}
                for variant in VARIANTS:
                    result = replay_entry_variant(row, bars, starts, variant=variant)
                    if result is not None:
                        retests[variant] = result
                enriched.append({**row, "entry_retests": retests})
            return enriched, {"symbol": symbol, "status": "OK", "bars": len(bars), "rows": len(enriched)}

        results = await asyncio.gather(
            *(process_symbol(symbol, symbol_rows) for symbol, symbol_rows in by_symbol.items())
        )

    enriched_rows: list[dict[str, Any]] = []
    symbol_status: list[dict[str, Any]] = []
    for symbol_rows, status in results:
        enriched_rows.extend(symbol_rows)
        symbol_status.append(status)

    analysis = build_entry_retest_report(
        enriched_rows,
        start_at=job["start_at"],
        development_end_at=job["development_end_at"],
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_version": STRATEGY_VERSION,
        "dataset_job_id": int(job["id"]),
        "rows": len(enriched_rows),
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
                "rows": len(enriched_rows),
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
        await build_production_entry_report()
    except Exception as exc:
        logger.exception("entry retest v4 research failed")
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
            logger.exception("failed to persist entry v4 failure status")
    finally:
        _task = None


def attach_v073_research_entry_v4_routes(app: FastAPI, require_api_key: Callable[..., Any]) -> None:
    @app.post(
        "/v1/day-trade/research/entry/v4/run",
        status_code=202,
        dependencies=[Depends(require_api_key)],
    )
    async def run_entry_v4() -> dict[str, Any]:
        global _task
        if _task is not None and not _task.done():
            raise HTTPException(status_code=409, detail="entry v4 research already running")
        _task = asyncio.create_task(_run_background(), name="v073-entry-v4")
        return {
            "accepted": True,
            "research_only": True,
            "live_strategy_mutated": False,
            "execution": "railway_background_research",
        }

    @app.get(
        "/v1/day-trade/research/entry/v4/status",
        dependencies=[Depends(require_api_key)],
    )
    async def status_entry_v4() -> dict[str, Any]:
        conn = await _connect()
        try:
            payload = await _cache_get(conn, STATUS_CACHE_KEY)
        finally:
            await conn.close()
        return payload or {"status": "NOT_RUN", "research_only": True}

    @app.get(
        "/v1/day-trade/research/entry/v4/report",
        dependencies=[Depends(require_api_key)],
    )
    async def report_entry_v4() -> dict[str, Any]:
        conn = await _connect()
        try:
            payload = await _cache_get(conn, REPORT_CACHE_KEY)
        finally:
            await conn.close()
        if payload is None:
            raise HTTPException(status_code=404, detail="entry v4 report not found")
        return payload
