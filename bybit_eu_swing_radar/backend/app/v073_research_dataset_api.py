"""FastAPI routes for the materialized v0.7.3 research dataset v1."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

import asyncpg
from fastapi import Depends, FastAPI, HTTPException

from app.config import settings
from research_dataset_v1 import (
    DATASET_VERSION,
    JOB_NAME,
    STRATEGY_VERSION,
    WARNINGS,
    build_profile_report,
    run_dataset_batch,
)
from research_interactions_v1 import build_interaction_report

logger = logging.getLogger(__name__)
_dataset_task: asyncio.Task[None] | None = None

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
    global _dataset_task
    try:
        result = await run_dataset_batch()
        logger.info("research dataset v1 batch finished: %s", result)
    except Exception:
        logger.exception("research dataset v1 batch failed")
    finally:
        _dataset_task = None

async def dataset_status_payload() -> dict[str, Any]:
    conn = await _connect()
    try:
        try:
            raw = await conn.fetchrow(
                """
                SELECT * FROM day_trade_diagnostic_jobs
                WHERE strategy_version=$1 AND job_name=$2
                ORDER BY id DESC LIMIT 1
                """,
                STRATEGY_VERSION,
                JOB_NAME,
            )
        except asyncpg.exceptions.UndefinedTableError:
            raw = None
        if not raw:
            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "strategy_version": STRATEGY_VERSION,
                "dataset_version": DATASET_VERSION,
                "job_name": JOB_NAME,
                "exists": False,
                "progress_pct": 0.0,
                "job": {},
                "symbol_status": [],
                "warnings": list(WARNINGS),
            }
        job = dict(raw)
        rows = await conn.fetch(
            """
            SELECT symbol,status,bars_fetched,evaluation_bars,event_count,
                   primary_event_count,started_at,completed_at,last_error
            FROM day_trade_diagnostic_symbols
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
        "dataset_version": DATASET_VERSION,
        "job_name": JOB_NAME,
        "exists": True,
        "progress_pct": round((completed + failed) / total * 100.0, 2) if total else 0.0,
        "job": job,
        "symbol_status": [dict(row) for row in rows],
        "warnings": list(job["warnings"]),
    }

async def dataset_report_payload() -> dict[str, Any]:
    conn = await _connect()
    try:
        try:
            raw = await conn.fetchrow(
                """
                SELECT * FROM day_trade_diagnostic_jobs
                WHERE strategy_version=$1 AND job_name=$2
                ORDER BY id DESC LIMIT 1
                """,
                STRATEGY_VERSION,
                JOB_NAME,
            )
        except asyncpg.exceptions.UndefinedTableError as exc:
            raise HTTPException(status_code=404, detail="research dataset tables are not initialized") from exc
        if not raw:
            raise HTTPException(status_code=404, detail="research dataset v1 job not found")
        job = dict(raw)
        if job.get("status") not in {"COMPLETED", "PARTIAL"}:
            raise HTTPException(
                status_code=409,
                detail=f"research dataset v1 job must be terminal, got {job.get('status')}",
            )
        rows = await conn.fetch(
            """
            SELECT dataset_split,symbol,side,opened_at,base_net_r,
                   sweep_depth_atr,bars_from_sweep_to_confirmation,
                   volume_ratio_5m,turnover_24h_usdc,modeled_spread_bps,
                   expansion_score,side_direction_score,quality_score,
                   setup_score,expected_rr,btc_volatility_regime,
                   btc_structure_1h,btc_structure_4h,timeframe_conflict
            FROM day_trade_diagnostic_events
            WHERE job_id=$1 AND candidate_built AND pass_structure_5m
            ORDER BY opened_at,id
            """,
            int(job["id"]),
        )
    finally:
        await conn.close()

    materialized_rows = [dict(row) for row in rows]
    report = build_profile_report(materialized_rows)
    interaction_report = build_interaction_report(
        materialized_rows,
        start_at=job["start_at"],
        development_end_at=job["development_end_at"],
    )
    job["parameters"] = _json_value(job.get("parameters"), {})
    job["universe"] = _json_value(job.get("universe"), [])
    job["warnings"] = _json_value(job.get("warnings"), [])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "job": job,
        **report,
        "interaction_analysis": interaction_report,
    }

def attach_v073_research_dataset_routes(
    app: FastAPI,
    require_api_key: Callable[..., Any],
) -> None:
    @app.post(
        "/v1/day-trade/research/dataset/v1/run-batch",
        status_code=202,
        dependencies=[Depends(require_api_key)],
    )
    async def run_batch() -> dict[str, Any]:
        global _dataset_task
        if _dataset_task is not None and not _dataset_task.done():
            raise HTTPException(status_code=409, detail="research dataset v1 batch already running")
        _dataset_task = asyncio.create_task(_run_background(), name="v073-research-dataset-v1-batch")
        return {
            "accepted": True,
            "research_only": True,
            "live_strategy_mutated": False,
            "strategy_version": STRATEGY_VERSION,
            "dataset_version": DATASET_VERSION,
            "job_name": JOB_NAME,
            "execution": "railway_background_batch",
        }

    @app.get(
        "/v1/day-trade/research/dataset/v1/status",
        dependencies=[Depends(require_api_key)],
    )
    async def status() -> dict[str, Any]:
        return await dataset_status_payload()

    @app.get(
        "/v1/day-trade/research/dataset/v1/report",
        dependencies=[Depends(require_api_key)],
    )
    async def report() -> dict[str, Any]:
        return await dataset_report_payload()
