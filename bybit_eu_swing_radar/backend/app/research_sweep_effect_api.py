"""Hidden research API for prospective v0.7.3 sweep-effect evidence."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import asyncpg
from fastapi import Depends, FastAPI, HTTPException

from app.config import settings
from research.sweep_forward_effect import (
    SPEC_VERSION,
    TRIGGER_MODEL,
    evaluate_effects,
    sample_gate,
    spec,
)

STRATEGY_VERSION = "0.7.3"


async def _connect() -> asyncpg.Connection:
    return await asyncpg.connect(settings.database_url)


async def _load_label_blind_counts(connection: asyncpg.Connection) -> dict[str, Any]:
    row = await connection.fetchrow(
        """
        SELECT
            COUNT(*)::int AS closed_signal_count,
            COUNT(*) FILTER (WHERE side='long')::int AS long_count,
            COUNT(*) FILTER (WHERE side='short')::int AS short_count,
            COUNT(DISTINCT ((opened_at AT TIME ZONE 'UTC')::date))::int AS distinct_utc_days,
            COUNT(DISTINCT symbol)::int AS symbol_count,
            COUNT(*) FILTER (WHERE signal_class='STRICT')::int AS strict_count,
            COUNT(*) FILTER (WHERE signal_class='SHADOW')::int AS shadow_count,
            COUNT(*) FILTER (
                WHERE NULLIF(candidate_payload->'trigger'->'sweep_confirmation'->>'sweep_depth_atr','') IS NOT NULL
                  AND NULLIF(candidate_payload->'trigger'->'sweep_confirmation'->>'bars_from_sweep_to_confirmation','') IS NOT NULL
                  AND NULLIF(candidate_payload->'trigger'->'sweep_confirmation'->>'volume_ratio_5m','') IS NOT NULL
                  AND NULLIF(candidate_payload->'trigger'->'sweep_confirmation'->>'structure_15m_state','') IS NOT NULL
            )::int AS attribute_complete_count,
            MIN(opened_at) AS first_opened_at,
            MAX(opened_at) AS last_opened_at
        FROM day_trade_signal_journal
        WHERE strategy_version=$1
          AND status='CLOSED'
          AND setup_type='LIQUIDITY_SWEEP_RECLAIM'
          AND candidate_payload->'trigger'->>'model'=$2
        """,
        STRATEGY_VERSION,
        TRIGGER_MODEL,
    )
    if row is None:
        return {}
    result = dict(row)
    for key in ("first_opened_at", "last_opened_at"):
        value = result.get(key)
        result[key] = value.isoformat() if value is not None else None
    return result


async def _load_outcomes(connection: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await connection.fetch(
        """
        SELECT
            symbol,side,opened_at,signal_class,exit_reason,net_r,mfe_r,mae_r,
            candidate_payload->'trigger'->'sweep_confirmation'->>'sweep_depth_atr' AS sweep_depth_atr,
            candidate_payload->'trigger'->'sweep_confirmation'->>'bars_from_sweep_to_confirmation' AS bars_from_sweep_to_confirmation,
            candidate_payload->'trigger'->'sweep_confirmation'->>'volume_ratio_5m' AS volume_ratio_5m,
            candidate_payload->'trigger'->'sweep_confirmation'->>'structure_15m_state' AS structure_15m_state
        FROM day_trade_signal_journal
        WHERE strategy_version=$1
          AND status='CLOSED'
          AND net_r IS NOT NULL
          AND setup_type='LIQUIDITY_SWEEP_RECLAIM'
          AND candidate_payload->'trigger'->>'model'=$2
        ORDER BY opened_at, id
        """,
        STRATEGY_VERSION,
        TRIGGER_MODEL,
    )
    output: list[dict[str, Any]] = []
    for raw in rows:
        item = dict(raw)
        opened = item.get("opened_at")
        if opened is not None:
            item["opened_at"] = opened.isoformat()
        output.append(item)
    return output


async def build_status_from_loaders(
    count_loader: Callable[[], Awaitable[dict[str, Any]]],
    outcome_loader: Callable[[], Awaitable[list[dict[str, Any]]]],
    *,
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    counts = await count_loader()
    gate = sample_gate(counts)
    base = {
        "spec_version": SPEC_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit_sha": source_commit_sha,
        "research_only": True,
        "live_strategy_mutated": False,
        "prospective_journal_only": True,
        "label_gate_before_outcomes": True,
        "promotion_allowed": False,
        "sample": {**counts, "gate": gate},
    }
    if not gate["ready"]:
        return {
            **base,
            "status": "WAITING_FOR_FORWARD_SAMPLE",
            "outcomes_loaded": False,
            "effects": None,
        }

    outcomes = await outcome_loader()
    effects = evaluate_effects(outcomes)
    status = (
        "COMPLETE"
        if effects["all_hypotheses_evaluable"]
        else "WAITING_FOR_HYPOTHESIS_COVERAGE"
    )
    return {
        **base,
        "status": status,
        "outcomes_loaded": True,
        "effects": effects,
    }


async def status_payload() -> dict[str, Any]:
    connection = await _connect()
    try:
        return await build_status_from_loaders(
            lambda: _load_label_blind_counts(connection),
            lambda: _load_outcomes(connection),
            source_commit_sha=os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
        )
    finally:
        await connection.close()


def attach_sweep_effect_research(
    app: FastAPI, require_api_key: Callable[..., Any]
) -> None:
    @app.get(
        "/v1/research/sweep-effect/spec",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def sweep_effect_spec() -> dict[str, Any]:
        return spec()

    @app.get(
        "/v1/research/sweep-effect/status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def sweep_effect_status() -> dict[str, Any]:
        try:
            return await status_payload()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research sweep-effect status unavailable: {type(exc).__name__}",
            ) from exc

    # Keep research route integration out of live strategy modules. The sweep
    # research router is already attached by app.main, so it can safely attach
    # the independent Cross-Layer Context research router as well.
    from app.research_cross_layer_context_api import attach_cross_layer_context_research

    attach_cross_layer_context_research(app, require_api_key)
