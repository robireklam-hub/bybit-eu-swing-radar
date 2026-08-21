"""Standalone prospective research worker.

This process is deliberately separate from the live day-radar worker. It
reuses the live market-analysis helpers and runs multiple preregistered,
label-free research recorders in one existing Railway cron sidecar. It writes
only research tables plus dedicated research status cache keys.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Iterable

import asyncpg
import httpx

import day_worker as live
from research.day_barrier_clear_observer_v1 import (
    pending_parent_symbols,
    persist_pending_resolutions,
)
from research.day_barrier_clear_parent_recorder_v1 import (
    ensure_schema_and_boundary,
    persist_parent_batch,
)
from research.prospective_funnel_v073 import persist_v073_prospective_funnel

STATUS_CACHE_KEY = "day_trade_prospective_funnel_status"
BARRIER_PARENT_STATUS_CACHE_KEY = "day_barrier_clear_rearm_parent_status"
BARRIER_OBSERVER_STATUS_CACHE_KEY = "day_barrier_clear_rearm_observer_status"
EXECUTION_MODE = "STANDALONE_RAILWAY_CRON"
MAX_RUNTIME_SECONDS = min(max(int(os.getenv("PROSPECTIVE_FUNNEL_MAX_RUNTIME_SECONDS", "180")), 60), 240)


def _decode_cache_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _strict_setups_from_scan(scan: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("strict_longs", "strict_shorts"):
        value = scan.get(key) or []
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    return rows


async def _load_authoritative_live_setups(connection: asyncpg.Connection) -> tuple[list[dict[str, Any]], str | None]:
    value = await connection.fetchval(
        "SELECT payload FROM radar_cache WHERE cache_key = $1",
        "day_trade_scan",
    )
    scan = _decode_cache_payload(value)
    return _strict_setups_from_scan(scan), scan.get("data_as_of")


async def _load_barrier_tracking_symbols(captured_at: datetime) -> list[str]:
    """Initialize the frozen cohort boundary and keep unresolved symbols tracked."""
    connection = await asyncpg.connect(live.DATABASE_URL, timeout=30, command_timeout=20)
    try:
        await ensure_schema_and_boundary(connection, captured_at)
        return await pending_parent_symbols(connection)
    finally:
        await connection.close()


def _force_required_deep_symbols(
    deep_universe: list[live.FastResult],
    fast_results: Iterable[live.FastResult],
    required_symbols: Iterable[str],
) -> list[live.FastResult]:
    output = list(deep_universe)
    seen = {item.instrument.symbol for item in output}
    available = {item.instrument.symbol: item for item in fast_results}
    for symbol in sorted({str(item).upper() for item in required_symbols}):
        if symbol in seen:
            continue
        item = available.get(symbol)
        if item is None:
            continue
        output.append(item)
        seen.add(symbol)
    return output


def _build_v075_barrier_candidates(
    analyses: Iterable[live.DayAnalysis],
    captured_at: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for analysis in analyses:
        for side in ("long", "short"):
            candidate = live.build_day_candidate(
                analysis,
                side,
                captured_at,
                strategy_version=live.V075_DAY_STRATEGY_VERSION,
            )
            if candidate is not None:
                rows.append(candidate)
    return rows


async def collect_forward_analyses(
    *,
    required_symbols: Iterable[str] = (),
) -> tuple[list[live.DayAnalysis], dict[str, Any]]:
    """Re-run live analysis helpers in this independent research process."""
    required = sorted({str(item).upper() for item in required_symbols})
    timeout = httpx.Timeout(30.0, connect=15.0)
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=10)
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        headers={"User-Agent": f"Bybit-EU-Prospective-Funnel/{live.DAY_STRATEGY_VERSION}"},
    ) as client:
        bybit = live.BybitAPI(client)
        coinalyze = live.CoinalyzeAPI(client)
        instruments, tickers = await asyncio.gather(bybit.instruments(), bybit.tickers())
        universe, _, universe_stats = live.normalize_usdc_universe(instruments, tickers)
        if not universe:
            raise RuntimeError("No eligible Bybit EU USDC markets for prospective capture")

        fast_semaphore = asyncio.Semaphore(live.DAY_FAST_CONCURRENCY)
        fetched = await asyncio.gather(
            *(live.fetch_fast(bybit, item, fast_semaphore) for item in universe)
        )
        fast_by_symbol = {item[0].symbol: item for item in fetched if item is not None}
        for retry_pass in range(live.DAY_RETRY_PASSES):
            missing = [item for item in universe if item.symbol not in fast_by_symbol]
            if not missing:
                break
            await asyncio.sleep(1.5 * (retry_pass + 1))
            retry_semaphore = asyncio.Semaphore(max(1, live.DAY_FAST_CONCURRENCY // 2))
            retried = await asyncio.gather(
                *(live.fetch_fast(bybit, item, retry_semaphore) for item in missing)
            )
            for item in retried:
                if item is not None:
                    fast_by_symbol[item[0].symbol] = item

        fast_results: list[live.FastResult] = []
        fast_calculation_failures = 0
        for instrument, bars_5m, bars_15m in fast_by_symbol.values():
            try:
                fast_results.append(live.calculate_fast_result(instrument, bars_5m, bars_15m))
            except Exception:
                fast_calculation_failures += 1

        deep_universe = live.select_deep_universe(fast_results)
        deep_universe = _force_required_deep_symbols(deep_universe, fast_results, required)
        context_semaphore = asyncio.Semaphore(live.DAY_CONTEXT_CONCURRENCY)
        context_fetched = await asyncio.gather(
            *(live.fetch_context(bybit, item, context_semaphore) for item in deep_universe)
        )
        context_valid = [item for item in context_fetched if item is not None]
        btc_context = next(
            (item for item in context_valid if item[0].instrument.symbol == "BTCUSDC"),
            None,
        )
        btc_return_1h = live.bar_return_pct(btc_context[0].bars_15m, 4) if btc_context else 0.0
        btc_return_4h = live.bar_return_pct(btc_context[1], 4) if btc_context else 0.0

        analyses: list[live.DayAnalysis] = []
        deep_calculation_failures = 0
        for fast, bars_1h, bars_4h in context_valid:
            try:
                analyses.append(
                    live.analyze_day_market(
                        fast,
                        bars_1h,
                        bars_4h,
                        btc_return_1h,
                        btc_return_4h,
                    )
                )
            except Exception:
                deep_calculation_failures += 1

        coinalyze_ok, coinalyze_error = await live.enrich_coinalyze(analyses, coinalyze)
        borrow_ok, borrow_error = await live.apply_shortability(analyses, bybit)
        analyzed_symbols = {item.instrument.symbol for item in analyses}
        return analyses, {
            "eligible_usdc_pairs": len(universe),
            "fast_scanned_pairs": len(fast_results),
            "fast_calculation_failures": fast_calculation_failures,
            "deep_requested_pairs": len(deep_universe),
            "deep_analyzed_pairs": len(analyses),
            "deep_calculation_failures": deep_calculation_failures,
            "coinalyze_request_ok": bool(coinalyze_ok),
            "coinalyze_error": coinalyze_error,
            "borrowability_request_ok": bool(borrow_ok),
            "borrowability_error": borrow_error,
            "required_barrier_tracking_symbols": required,
            "required_barrier_tracking_symbols_analyzed": sorted(
                symbol for symbol in required if symbol in analyzed_symbols
            ),
            "universe_stats": universe_stats,
        }


async def persist_standalone_capture(
    analyses: list[live.DayAnalysis],
    collection: dict[str, Any],
    *,
    captured_at: datetime,
    required_barrier_symbols: Iterable[str] = (),
) -> dict[str, Any]:
    connection = await asyncpg.connect(live.DATABASE_URL, timeout=30, command_timeout=20)
    try:
        authoritative_live_setups, live_scan_as_of = await _load_authoritative_live_setups(connection)
        barrier_candidates = _build_v075_barrier_candidates(analyses, captured_at)
        async with connection.transaction():
            status = await persist_v073_prospective_funnel(
                connection,
                analyses,
                captured_at=captured_at,
                source_commit_sha=live.SOURCE_COMMIT_SHA,
                volume_confirmation_ratio=live.DAY_TRIGGER_VOLUME_RATIO,
                live_setups=authoritative_live_setups,
            )
            parent_status = await persist_parent_batch(
                connection,
                barrier_candidates,
                captured_at=captured_at,
                source_commit_sha=live.SOURCE_COMMIT_SHA,
            )
            observer_status = await persist_pending_resolutions(
                connection,
                analyses,
                observed_at=captured_at,
                source_commit_sha=live.SOURCE_COMMIT_SHA,
            )
            forced_symbols = sorted({str(item).upper() for item in required_barrier_symbols})
            parent_cache_status = {
                **parent_status,
                "execution_mode": "SHARED_STANDALONE_RESEARCH_SIDECAR",
                "current_live_strategy_version": live.DAY_STRATEGY_VERSION,
                "v075_candidates_built": len(barrier_candidates),
                "forced_tracking_symbols": forced_symbols,
                "live_worker_inline_recorder": False,
                "live_worker_mutation": False,
            }
            observer_cache_status = {
                **observer_status,
                "execution_mode": "SHARED_STANDALONE_RESEARCH_SIDECAR",
                "current_live_strategy_version": live.DAY_STRATEGY_VERSION,
                "forced_tracking_symbols": forced_symbols,
                "live_worker_inline_recorder": False,
                "live_worker_mutation": False,
            }
            barrier_status = {
                **parent_cache_status,
                "observer": observer_cache_status,
            }
            status = {
                **status,
                "execution_mode": EXECUTION_MODE,
                "live_worker_inline_recorder": False,
                "live_worker_mutation": False,
                "authoritative_live_scan_as_of": live_scan_as_of,
                "authoritative_live_strict_setups": len(authoritative_live_setups),
                "collection": collection,
                "barrier_clear_rearm": barrier_status,
            }
            await live.upsert_cache(connection, STATUS_CACHE_KEY, status)
            await live.upsert_cache(connection, BARRIER_PARENT_STATUS_CACHE_KEY, parent_cache_status)
            await live.upsert_cache(connection, BARRIER_OBSERVER_STATUS_CACHE_KEY, observer_cache_status)
        return status
    finally:
        await connection.close()


async def run() -> None:
    if not live.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    started = datetime.now(timezone.utc)
    required_barrier_symbols = await _load_barrier_tracking_symbols(started)
    print(
        f"Prospective research worker starting: strategy={live.DAY_STRATEGY_VERSION}, "
        f"source_commit_sha={live.SOURCE_COMMIT_SHA or 'UNKNOWN'}, "
        f"barrier_tracking={len(required_barrier_symbols)}",
        flush=True,
    )
    analyses, collection = await collect_forward_analyses(required_symbols=required_barrier_symbols)
    captured_at = datetime.now(timezone.utc)
    print(
        f"Prospective pre-persist: deep={len(analyses)}, "
        f"borrowability_ok={collection['borrowability_request_ok']}",
        flush=True,
    )
    status = await persist_standalone_capture(
        analyses,
        collection,
        captured_at=captured_at,
        required_barrier_symbols=required_barrier_symbols,
    )
    duration = (datetime.now(timezone.utc) - started).total_seconds()
    barrier = status.get("barrier_clear_rearm") or {}
    observer = barrier.get("observer") or {}
    print(
        "Prospective research complete: "
        f"observed={status['current_run']['observed_snapshots']}, "
        f"inserted={status['current_run']['inserted_snapshots']}, "
        f"distinct_events={status['cumulative']['distinct_sweep_events']}, "
        f"barrier_parents={barrier.get('total_frozen_parents', 0)}, "
        f"barrier_new={barrier.get('inserted_this_run', 0)}, "
        f"barrier_resolved={sum((observer.get('resolved_this_run') or {}).values())}, "
        f"duration={duration:.1f}s",
        flush=True,
    )


async def _bounded_main() -> None:
    await asyncio.wait_for(run(), timeout=MAX_RUNTIME_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(_bounded_main())
    except Exception:
        print("FATAL prospective research worker", file=sys.stderr, flush=True)
        raise
