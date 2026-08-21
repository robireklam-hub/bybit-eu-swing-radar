"""Standalone prospective parent capture for day-barrier-clear-rearm-v1.

This worker deliberately reuses current market-data collection but rebuilds
candidate decisions under frozen strategy_version=0.7.5. It writes research
rows only and never mutates live day-radar caches, rankings or execution state.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import asyncpg

import day_worker as live
from prospective_funnel_worker import collect_forward_analyses
from research.day_barrier_clear_parent_recorder_v1 import persist_parent_batch

STATUS_CACHE_KEY = "day_barrier_clear_rearm_parent_status"
MAX_RUNTIME_SECONDS = min(max(int(os.getenv("BARRIER_CLEAR_PARENT_MAX_RUNTIME_SECONDS", "180")), 60), 240)


def build_v075_candidates(analyses: list[live.DayAnalysis], captured_at: datetime) -> list[dict]:
    candidates: list[dict] = []
    for analysis in analyses:
        for side in ("long", "short"):
            candidate = live.build_day_candidate(
                analysis,
                side,
                captured_at,
                strategy_version=live.V075_DAY_STRATEGY_VERSION,
            )
            if candidate is not None:
                candidates.append(candidate)
    return candidates


async def run() -> None:
    if not live.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    started_at = datetime.now(timezone.utc)
    analyses, collection = await collect_forward_analyses()
    captured_at = datetime.now(timezone.utc)
    candidates = build_v075_candidates(analyses, captured_at)

    connection = await asyncpg.connect(live.DATABASE_URL, timeout=30, command_timeout=20)
    try:
        async with connection.transaction():
            status = await persist_parent_batch(
                connection,
                candidates,
                captured_at=captured_at,
                source_commit_sha=live.SOURCE_COMMIT_SHA,
            )
            status = {
                **status,
                "execution_mode": "STANDALONE_RESEARCH_WORKER",
                "live_worker_inline_recorder": False,
                "candidate_strategy_version": live.V075_DAY_STRATEGY_VERSION,
                "current_live_strategy_version": live.DAY_STRATEGY_VERSION,
                "analyses_collected": len(analyses),
                "v075_candidates_built": len(candidates),
                "collection": collection,
                "duration_seconds": (datetime.now(timezone.utc) - started_at).total_seconds(),
            }
            await live.upsert_cache(connection, STATUS_CACHE_KEY, status)
    finally:
        await connection.close()

    print(json.dumps(status, sort_keys=True, default=str), flush=True)


async def _bounded_main() -> None:
    await asyncio.wait_for(run(), timeout=MAX_RUNTIME_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(_bounded_main())
    except Exception:
        print("FATAL barrier-clear parent worker", file=sys.stderr, flush=True)
        raise
