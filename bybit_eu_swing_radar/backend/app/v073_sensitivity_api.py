"""Read-only FastAPI attachment for v0.7.3 parameter sensitivity research."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query

from app.config import settings
from diagnostics_v073 import DIAGNOSTIC_JOB_NAME, STRATEGY_VERSION
from sensitivity_v073 import build_sensitivity_report


async def _connect() -> asyncpg.Connection:
    return await asyncpg.connect(settings.database_url)


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value


async def sensitivity_payload(top_k: int = 20) -> dict[str, Any]:
    conn = await _connect()
    try:
        job_raw = await conn.fetchrow(
            """
            SELECT id,job_name,strategy_version,status,completed_at,
                   development_end_at,start_at,end_at,parameters,warnings
            FROM day_trade_diagnostic_jobs
            WHERE strategy_version=$1 AND job_name=$2
            ORDER BY id DESC LIMIT 1
            """,
            STRATEGY_VERSION,
            DIAGNOSTIC_JOB_NAME,
        )
        if not job_raw:
            raise HTTPException(status_code=404, detail="v0.7.3 diagnostic job not found")
        job = dict(job_raw)
        if job.get("status") != "COMPLETED":
            raise HTTPException(
                status_code=409,
                detail=f"v0.7.3 diagnostic job must be COMPLETED, got {job.get('status')}",
            )
        rows_raw = await conn.fetch(
            """
            SELECT strategy_version,symbol,side,opened_at,dataset_split,
                   included_primary,candidate_built,pass_reclaim,pass_structure_5m,
                   pass_structure_15m,pass_tradeable,pass_side_execution_model,
                   expansion_score,side_direction_score,quality_score,setup_score,
                   volume_ratio_5m,entry_price,stop_price,base_cost_bps,
                   base_exit_reason,base_gross_r,base_net_r,base_mfe_r,base_mae_r,
                   candidate_payload,sweep_depth_atr,bars_from_sweep_to_confirmation,
                   timeframe_conflict
            FROM day_trade_diagnostic_events
            WHERE job_id=$1
              AND strategy_version=$2
              AND included_primary
              AND candidate_built
              AND base_net_r IS NOT NULL
            ORDER BY opened_at
            """,
            int(job["id"]),
            STRATEGY_VERSION,
        )
    except asyncpg.exceptions.UndefinedTableError as exc:
        raise HTTPException(status_code=404, detail="diagnostic tables are not initialized") from exc
    finally:
        await conn.close()

    rows: list[dict[str, Any]] = []
    for raw in rows_raw:
        row = dict(raw)
        row["candidate_payload"] = _json_value(row.get("candidate_payload")) or {}
        rows.append(row)

    job["parameters"] = _json_value(job.get("parameters")) or {}
    job["warnings"] = _json_value(job.get("warnings")) or []
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_version": STRATEGY_VERSION,
        "research_only": True,
        "live_strategy_mutated": False,
        "database_mutated": False,
        "source_job": job,
        **build_sensitivity_report(rows, top_k=top_k),
    }


def attach_v073_sensitivity_routes(
    app: FastAPI,
    require_api_key: Callable[..., Any],
) -> None:
    @app.get(
        "/v1/day-trade/backtest/diagnostics/v073/sensitivity",
        dependencies=[Depends(require_api_key)],
    )
    async def v073_sensitivity(
        top_k: int = Query(20, ge=1, le=50),
    ) -> dict[str, Any]:
        return await sensitivity_payload(top_k=top_k)
