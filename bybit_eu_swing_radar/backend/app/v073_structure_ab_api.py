"""FastAPI attachment for the fixed v0.7.3 pivot-structure A/B research replay."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

import asyncpg
from fastapi import Depends, FastAPI, HTTPException

from app.config import settings
from structure_ab_v073 import (
    STRATEGY_VERSION,
    STRUCTURE_AB_JOB_NAME,
    WARNINGS,
    build_report_from_symbol_results,
    run_structure_ab_batch,
)

logger = logging.getLogger(__name__)
_structure_ab_task: asyncio.Task[None] | None = None


async def _connect() -> asyncpg.Connection:
    return await asyncpg.connect(settings.database_url)


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


async def _run_background() -> None:
    global _structure_ab_task
    try:
        result = await run_structure_ab_batch()
        logger.info("v0.7.3 pivot structure A/B batch finished: %s", result)
    except Exception:
        logger.exception("v0.7.3 pivot structure A/B batch failed")
    finally:
        _structure_ab_task = None


async def structure_ab_status_payload() -> dict[str, Any]:
    conn = await _connect()
    try:
        try:
            job_raw = await conn.fetchrow(
                """
                SELECT * FROM day_trade_structure_ab_jobs
                WHERE strategy_version=$1 AND job_name=$2
                ORDER BY id DESC LIMIT 1
                """,
                STRATEGY_VERSION,
                STRUCTURE_AB_JOB_NAME,
            )
        except asyncpg.exceptions.UndefinedTableError:
            job_raw = None
        if not job_raw:
            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "strategy_version": STRATEGY_VERSION,
                "job_name": STRUCTURE_AB_JOB_NAME,
                "exists": False,
                "progress_pct": 0.0,
                "job": {},
                "symbol_status": [],
                "warnings": list(WARNINGS),
            }
        job = dict(job_raw)
        rows = await conn.fetch(
            """
            SELECT symbol,status,bars_fetched,started_at,completed_at,last_error
            FROM day_trade_structure_ab_symbols
            WHERE job_id=$1 ORDER BY status,symbol
            """,
            int(job["id"]),
        )
    finally:
        await conn.close()

    job["parameters"] = _json_value(job.get("parameters"), {})
    job["universe"] = _json_value(job.get("universe"), [])
    job["warnings"] = _json_value(job.get("warnings"), [])
    total = int(job.get("total_symbols") or 0)
    completed = int(job.get("completed_symbols") or 0)
    failed = int(job.get("failed_symbols") or 0)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_version": STRATEGY_VERSION,
        "job_name": STRUCTURE_AB_JOB_NAME,
        "exists": True,
        "progress_pct": (
            round((completed + failed) / total * 100.0, 2) if total else 0.0
        ),
        "job": job,
        "symbol_status": [dict(row) for row in rows],
        "warnings": list(job["warnings"]),
    }


async def structure_ab_report_payload() -> dict[str, Any]:
    conn = await _connect()
    try:
        try:
            job_raw = await conn.fetchrow(
                """
                SELECT * FROM day_trade_structure_ab_jobs
                WHERE strategy_version=$1 AND job_name=$2
                ORDER BY id DESC LIMIT 1
                """,
                STRATEGY_VERSION,
                STRUCTURE_AB_JOB_NAME,
            )
        except asyncpg.exceptions.UndefinedTableError as exc:
            raise HTTPException(
                status_code=404,
                detail="v0.7.3 structure A/B tables are not initialized",
            ) from exc
        if not job_raw:
            raise HTTPException(status_code=404, detail="v0.7.3 structure A/B job not found")
        job = dict(job_raw)
        if job.get("status") not in {"COMPLETED", "PARTIAL"}:
            raise HTTPException(
                status_code=409,
                detail=f"structure A/B job must be terminal, got {job.get('status')}",
            )
        rows = await conn.fetch(
            """
            SELECT symbol,status,result,last_error
            FROM day_trade_structure_ab_symbols
            WHERE job_id=$1 ORDER BY symbol
            """,
            int(job["id"]),
        )
    finally:
        await conn.close()

    results: list[dict[str, Any]] = []
    failed_symbols: list[dict[str, Any]] = []
    for row_raw in rows:
        row = dict(row_raw)
        if row.get("status") == "COMPLETED":
            value = _json_value(row.get("result"), {})
            if value:
                results.append(value)
        elif row.get("status") == "FAILED":
            failed_symbols.append(
                {
                    "symbol": row.get("symbol"),
                    "error": row.get("last_error"),
                }
            )

    report = build_report_from_symbol_results(
        results,
        job["start_at"],
        job["end_at"],
        expected_symbols=int(job.get("total_symbols") or 0),
    )
    job["parameters"] = _json_value(job.get("parameters"), {})
    job["universe"] = _json_value(job.get("universe"), [])
    job["warnings"] = _json_value(job.get("warnings"), [])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "job": job,
        "completed_symbol_results": len(results),
        "failed_symbols": failed_symbols,
        "warnings": list(job["warnings"]),
        **report,
    }


def attach_v073_structure_ab_routes(
    app: FastAPI,
    require_api_key: Callable[..., Any],
) -> None:
    @app.post(
        "/v1/day-trade/backtest/structure-ab/v073/run-batch",
        status_code=202,
        dependencies=[Depends(require_api_key)],
    )
    async def run_batch() -> dict[str, Any]:
        global _structure_ab_task
        if _structure_ab_task is not None and not _structure_ab_task.done():
            raise HTTPException(
                status_code=409,
                detail="v0.7.3 structure A/B batch already running",
            )
        _structure_ab_task = asyncio.create_task(
            _run_background(),
            name="v073-pivot-structure-ab-batch",
        )
        return {
            "accepted": True,
            "strategy_version": STRATEGY_VERSION,
            "job_name": STRUCTURE_AB_JOB_NAME,
            "research_only": True,
            "execution": "railway_background_batch",
        }

    @app.get(
        "/v1/day-trade/backtest/structure-ab/v073/status",
        dependencies=[Depends(require_api_key)],
    )
    async def status() -> dict[str, Any]:
        return await structure_ab_status_payload()

    @app.get(
        "/v1/day-trade/backtest/structure-ab/v073/report",
        dependencies=[Depends(require_api_key)],
    )
    async def report() -> dict[str, Any]:
        return await structure_ab_report_payload()
