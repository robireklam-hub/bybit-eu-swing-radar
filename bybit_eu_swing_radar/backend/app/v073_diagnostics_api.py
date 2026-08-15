"""FastAPI attachment for v0.7.3 Railway-side gate diagnostics."""
from __future__ import annotations

import asyncio
import json
import logging
import statistics
from datetime import datetime, timezone
from typing import Any, Callable

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query

from app.config import settings
from diagnostics_v073 import (
    DIAGNOSTIC_JOB_NAME,
    STRATEGY_VERSION,
    run_diagnostic_batch,
)

logger = logging.getLogger(__name__)
_diagnostic_task: asyncio.Task[None] | None = None


async def _run_background() -> None:
    global _diagnostic_task
    try:
        result = await run_diagnostic_batch()
        logger.info("v0.7.3 diagnostic batch finished: %s", result)
    except Exception:
        logger.exception("v0.7.3 diagnostic batch failed")
    finally:
        _diagnostic_task = None


async def _connect() -> asyncpg.Connection:
    return await asyncpg.connect(settings.database_url)


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


async def diagnostic_status_payload() -> dict[str, Any]:
    conn = await _connect()
    try:
        job_raw = await conn.fetchrow(
            """
            SELECT * FROM day_trade_diagnostic_jobs
            WHERE strategy_version=$1 AND job_name=$2
            ORDER BY id DESC LIMIT 1
            """,
            STRATEGY_VERSION,
            DIAGNOSTIC_JOB_NAME,
        )
        if not job_raw:
            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "strategy_version": STRATEGY_VERSION,
                "exists": False,
                "job": {},
                "progress_pct": 0.0,
                "symbol_status": [],
                "warnings": ["v0.7.3 diagnostic job has not been initialized."],
            }
        job = dict(job_raw)
        rows = await conn.fetch(
            """
            SELECT symbol,status,bars_fetched,evaluation_bars,event_count,
                   primary_event_count,strict_eligible_count,strict_trade_count,
                   last_error,started_at,completed_at
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
    progress = (
        round((completed + failed) / total * 100.0, 2) if total else 0.0
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_version": STRATEGY_VERSION,
        "exists": True,
        "job": job,
        "progress_pct": progress,
        "symbol_status": [dict(row) for row in rows],
        "warnings": list(job["warnings"]),
    }


def _matches(
    row: dict[str, Any],
    side: str,
    split: str,
    primary_only: bool,
) -> bool:
    return (
        (side == "both" or row.get("side") == side)
        and (split == "all" or row.get("dataset_split") == split)
        and (not primary_only or bool(row.get("included_primary")))
    )


def _segment(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key) or "UNKNOWN"), []).append(row)
    return [
        {
            "key": name,
            "sweep_count": len(values),
            "candidate_count": sum(
                1 for value in values if value.get("candidate_built")
            ),
            "strict_eligible_count": sum(
                1 for value in values if value.get("pass_strict_eligible")
            ),
            "strict_trade_count": sum(
                1 for value in values if value.get("pass_strict_trade")
            ),
        }
        for name, values in sorted(
            groups.items(), key=lambda item: len(item[1]), reverse=True
        )
    ]


def waterfall_from_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    gates = [
        ("LIQUIDITY_SWEEP", None),
        ("RECLAIM", "pass_reclaim"),
        ("STRUCTURE_SHIFT_5M", "pass_structure_5m"),
        ("VOLUME_1_3X", "pass_volume_confirmation"),
        ("STRUCTURE_15M_NON_OPPOSING", "pass_structure_15m"),
        ("CANDIDATE_BUILT", "candidate_built"),
        ("LIQUIDITY_EXECUTION", "pass_tradeable"),
        ("SIDE_EXECUTION_MODEL", "pass_side_execution_model"),
        ("EXPANSION_55", "pass_expansion"),
        ("DIRECTION_35", "pass_direction"),
        ("QUALITY_65", "pass_quality"),
        ("SETUP_70", "pass_setup"),
        ("TARGET_PATH", "pass_target_path"),
        ("NET_RR_1_8", "pass_rr"),
        ("STRICT_TRADE", "pass_strict_trade"),
    ]
    trigger_count = len(rows)
    active = list(rows)
    waterfall: list[dict[str, Any]] = []
    for gate, field in gates:
        reached = len(active)
        passed_rows = (
            active
            if field is None
            else [row for row in active if bool(row.get(field))]
        )
        passed = len(passed_rows)
        waterfall.append(
            {
                "gate": gate,
                "reached_count": reached,
                "passed_count": passed,
                "failed_count": reached - passed,
                "pass_rate_from_reached_pct": (
                    round(passed / reached * 100.0, 2) if reached else None
                ),
                "pass_rate_from_sweep_pct": (
                    round(passed / trigger_count * 100.0, 2)
                    if trigger_count
                    else None
                ),
            }
        )
        active = passed_rows

    failures: dict[str, int] = {}
    for row in rows:
        key = str(row.get("first_failed_gate") or "UNKNOWN")
        failures[key] = failures.get(key, 0) + 1
    first_failures = [
        {
            "key": key,
            "count": count,
            "pct_of_sweep": (
                round(count / trigger_count * 100.0, 2)
                if trigger_count
                else None
            ),
        }
        for key, count in sorted(
            failures.items(), key=lambda item: item[1], reverse=True
        )
    ]
    return waterfall, first_failures


async def diagnostic_waterfall_payload(
    side: str = "both",
    split: str = "all",
    primary_only: bool = False,
) -> dict[str, Any]:
    conn = await _connect()
    try:
        job_raw = await conn.fetchrow(
            """
            SELECT * FROM day_trade_diagnostic_jobs
            WHERE strategy_version=$1 AND job_name=$2
            ORDER BY id DESC LIMIT 1
            """,
            STRATEGY_VERSION,
            DIAGNOSTIC_JOB_NAME,
        )
        if not job_raw:
            return {
                "strategy_version": STRATEGY_VERSION,
                "job": {"status": "NOT_INITIALIZED"},
                "sweep_count": 0,
                "waterfall": [],
                "first_failures": [],
            }
        job = dict(job_raw)
        rows_raw = await conn.fetch(
            """
            SELECT symbol,side,opened_at,dataset_split,included_primary,
                   candidate_built,pass_reclaim,pass_structure_5m,
                   pass_volume_confirmation,pass_structure_15m,
                   pass_tradeable,pass_side_execution_model,pass_expansion,
                   pass_direction,pass_quality,pass_setup,pass_target_path,
                   pass_rr,pass_score_gates,pass_strict_eligible,
                   pass_strict_trade,near_strict,first_failed_gate,
                   timeframe_conflict
            FROM day_trade_diagnostic_events
            WHERE job_id=$1 ORDER BY opened_at
            """,
            int(job["id"]),
        )
    finally:
        await conn.close()

    rows = [
        dict(row)
        for row in rows_raw
        if _matches(dict(row), side, split, primary_only)
    ]
    waterfall, first_failures = waterfall_from_rows(rows)
    job["parameters"] = _json_value(job.get("parameters"), {})
    job["universe"] = _json_value(job.get("universe"), [])
    job["warnings"] = _json_value(job.get("warnings"), [])
    return {
        "strategy_version": STRATEGY_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "job": job,
        "requested_side": side,
        "requested_split": split,
        "primary_only": primary_only,
        "sweep_count": len(rows),
        "primary_count": sum(
            1 for row in rows if row.get("included_primary")
        ),
        "strict_eligible_count": sum(
            1 for row in rows if row.get("pass_strict_eligible")
        ),
        "strict_trade_count": sum(
            1 for row in rows if row.get("pass_strict_trade")
        ),
        "four_hour_conflict_count": sum(
            1 for row in rows if row.get("timeframe_conflict")
        ),
        "waterfall": waterfall,
        "first_failures": first_failures,
        "by_side": _segment(rows, "side"),
        "by_split": _segment(rows, "dataset_split"),
        "methodology": [
            "Every stored row starts with a detected liquidity sweep; incomplete/rejected sequences are retained.",
            "The funnel is sequential and matches v0.7.3 trigger order before scoring/execution gates.",
            "4H conflict is reported only as context and is not a hard gate.",
            "Use primary_only=false for gate diagnosis; primary_only=true is intended for non-overlapping outcome cohorts.",
        ],
        "warnings": list(job["warnings"]),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sample = len(rows)
    net = [float(row["base_net_r"]) for row in rows]
    positives = [value for value in net if value > 0]
    negatives = [value for value in net if value < 0]
    gains = sum(positives)
    losses = abs(sum(negatives))
    return {
        "sample_size": sample,
        "tp2_count": sum(
            1 for row in rows if row.get("base_exit_reason") == "TP2"
        ),
        "stop_count": sum(
            1
            for row in rows
            if row.get("base_exit_reason")
            in {"STOP", "AMBIGUOUS_STOP_FIRST"}
        ),
        "time_exit_count": sum(
            1
            for row in rows
            if row.get("base_exit_reason") == "TIME_EXIT"
        ),
        "positive_net_rate_pct": (
            round(len(positives) / sample * 100.0, 2) if sample else None
        ),
        "average_net_r": (
            round(statistics.fmean(net), 4) if net else None
        ),
        "median_net_r": (
            round(statistics.median(net), 4) if net else None
        ),
        "profit_factor": (
            round(gains / losses, 4) if losses > 0 else None
        ),
        "average_mfe_r": (
            round(
                statistics.fmean(
                    float(row.get("base_mfe_r") or 0.0) for row in rows
                ),
                4,
            )
            if rows
            else None
        ),
        "average_mae_r": (
            round(
                statistics.fmean(
                    float(row.get("base_mae_r") or 0.0) for row in rows
                ),
                4,
            )
            if rows
            else None
        ),
    }


def _cohort_match(row: dict[str, Any], cohort: str) -> bool:
    if row.get("base_net_r") is None:
        return False
    if cohort == "STRUCTURE_5M":
        return bool(row.get("pass_structure_5m"))
    if cohort == "VOLUME_PASS":
        return bool(
            row.get("pass_structure_5m")
            and row.get("pass_volume_confirmation")
        )
    if cohort == "STRUCTURE_15M_PASS":
        return bool(
            row.get("pass_structure_5m")
            and row.get("pass_volume_confirmation")
            and row.get("pass_structure_15m")
        )
    if cohort == "LIQUID_EXECUTABLE":
        return bool(
            row.get("pass_tradeable")
            and row.get("pass_side_execution_model")
        )
    if cohort == "SCORE_GATES_PASS":
        return bool(row.get("pass_score_gates"))
    if cohort == "STRICT_ELIGIBLE":
        return bool(row.get("pass_strict_eligible"))
    if cohort == "STRICT_TRADE":
        return bool(row.get("pass_strict_trade"))
    return False


async def diagnostic_edge_payload(
    side: str = "both",
    split: str = "all",
    primary_only: bool = True,
) -> dict[str, Any]:
    conn = await _connect()
    try:
        job_id = await conn.fetchval(
            """
            SELECT id FROM day_trade_diagnostic_jobs
            WHERE strategy_version=$1 AND job_name=$2
            ORDER BY id DESC LIMIT 1
            """,
            STRATEGY_VERSION,
            DIAGNOSTIC_JOB_NAME,
        )
        if job_id is None:
            return {
                "strategy_version": STRATEGY_VERSION,
                "status": "NOT_INITIALIZED",
                "cohorts": [],
            }
        rows_raw = await conn.fetch(
            """
            SELECT symbol,side,dataset_split,included_primary,
                   pass_structure_5m,pass_volume_confirmation,
                   pass_structure_15m,pass_tradeable,
                   pass_side_execution_model,pass_score_gates,
                   pass_strict_eligible,pass_strict_trade,
                   timeframe_conflict,base_exit_reason,base_net_r,
                   base_mfe_r,base_mae_r
            FROM day_trade_diagnostic_events
            WHERE job_id=$1 AND base_net_r IS NOT NULL
            ORDER BY opened_at
            """,
            int(job_id),
        )
    finally:
        await conn.close()

    rows = [
        dict(row)
        for row in rows_raw
        if _matches(dict(row), side, split, primary_only)
    ]
    cohort_names = [
        "STRUCTURE_5M",
        "VOLUME_PASS",
        "STRUCTURE_15M_PASS",
        "LIQUID_EXECUTABLE",
        "SCORE_GATES_PASS",
        "STRICT_ELIGIBLE",
        "STRICT_TRADE",
    ]
    cohorts = [
        {
            "cohort": cohort,
            "stats": _aggregate(
                [row for row in rows if _cohort_match(row, cohort)]
            ),
        }
        for cohort in cohort_names
    ]
    conflict_rows = [
        row for row in rows if bool(row.get("timeframe_conflict"))
    ]
    no_conflict_rows = [
        row for row in rows if not bool(row.get("timeframe_conflict"))
    ]
    return {
        "strategy_version": STRATEGY_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requested_side": side,
        "requested_split": split,
        "primary_only": primary_only,
        "evaluable_sample": len(rows),
        "cohorts": cohorts,
        "four_hour_context_ab": {
            "conflict": _aggregate(conflict_rows),
            "no_conflict": _aggregate(no_conflict_rows),
        },
        "methodology": [
            "Cohorts are nested research slices over the same v0.7.3 sweep sample.",
            "Primary rows exclude overlapping same-symbol/same-side outcomes.",
            "The 4H comparison is observational context only and does not reintroduce a hard veto.",
        ],
    }


def attach_v073_diagnostic_routes(
    app: FastAPI,
    require_api_key: Callable[..., Any],
) -> None:
    @app.post(
        "/v1/day-trade/backtest/diagnostics/v073/run-batch",
        status_code=202,
        dependencies=[Depends(require_api_key)],
    )
    async def run_batch() -> dict[str, Any]:
        global _diagnostic_task
        if _diagnostic_task is not None and not _diagnostic_task.done():
            raise HTTPException(
                status_code=409,
                detail="v0.7.3 diagnostic batch already running",
            )
        _diagnostic_task = asyncio.create_task(
            _run_background(),
            name="v073-diagnostic-batch",
        )
        return {
            "accepted": True,
            "strategy_version": STRATEGY_VERSION,
            "job_name": DIAGNOSTIC_JOB_NAME,
            "execution": "railway_background_diagnostic_batch",
        }

    @app.get(
        "/v1/day-trade/backtest/diagnostics/v073/status",
        dependencies=[Depends(require_api_key)],
    )
    async def status() -> dict[str, Any]:
        return await diagnostic_status_payload()

    @app.get(
        "/v1/day-trade/backtest/diagnostics/v073/waterfall",
        dependencies=[Depends(require_api_key)],
    )
    async def waterfall(
        side: str = Query("both", pattern="^(both|long|short)$"),
        split: str = Query(
            "all", pattern="^(all|DEVELOPMENT|VALIDATION)$"
        ),
        primary_only: bool = Query(False),
    ) -> dict[str, Any]:
        return await diagnostic_waterfall_payload(
            side=side,
            split=split,
            primary_only=primary_only,
        )

    @app.get(
        "/v1/day-trade/backtest/diagnostics/v073/edge",
        dependencies=[Depends(require_api_key)],
    )
    async def edge(
        side: str = Query("both", pattern="^(both|long|short)$"),
        split: str = Query(
            "all", pattern="^(all|DEVELOPMENT|VALIDATION)$"
        ),
        primary_only: bool = Query(True),
    ) -> dict[str, Any]:
        return await diagnostic_edge_payload(
            side=side,
            split=split,
            primary_only=primary_only,
        )
