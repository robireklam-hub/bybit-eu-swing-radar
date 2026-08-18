"""Standalone v0.7.3 prospective funnel research worker.

This process is deliberately separate from the live day-radar worker. It
reuses the live market-analysis helpers and the prospective recorder, but it
has its own Railway cron process and writes only research tables plus a
dedicated research status cache key.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx

import day_worker as live
from research.prospective_funnel_v073 import persist_v073_prospective_funnel

STATUS_CACHE_KEY = "day_trade_prospective_funnel_status"
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


async def collect_forward_analyses() -> tuple[list[live.DayAnalysis], dict[str, Any]]:
    """Re-run the live analysis helpers in this independent research process."""
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
            "universe_stats": universe_stats,
        }


async def persist_standalone_capture(
    analyses: list[live.DayAnalysis],
    collection: dict[str, Any],
    *,
    captured_at: datetime,
) -> dict[str, Any]:
    connection = await asyncpg.connect(live.DATABASE_URL, timeout=30)
    try:
        authoritative_live_setups, live_scan_as_of = await _load_authoritative_live_setups(connection)
        async with connection.transaction():
            status = await persist_v073_prospective_funnel(
                connection,
                analyses,
                captured_at=captured_at,
                source_commit_sha=live.SOURCE_COMMIT_SHA,
                volume_confirmation_ratio=live.DAY_TRIGGER_VOLUME_RATIO,
                live_setups=authoritative_live_setups,
            )
            status = {
                **status,
                "execution_mode": EXECUTION_MODE,
                "live_worker_inline_recorder": False,
                "live_worker_mutation": False,
                "authoritative_live_scan_as_of": live_scan_as_of,
                "authoritative_live_strict_setups": len(authoritative_live_setups),
                "collection": collection,
            }
            await live.upsert_cache(connection, STATUS_CACHE_KEY, status)
        return status
    finally:
        await connection.close()


async def run() -> None:
    if not live.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    started = datetime.now(timezone.utc)
    print(
        f"Prospective funnel worker starting: strategy={live.DAY_STRATEGY_VERSION}, "
        f"source_commit_sha={live.SOURCE_COMMIT_SHA or 'UNKNOWN'}",
        flush=True,
    )
    analyses, collection = await collect_forward_analyses()
    captured_at = datetime.now(timezone.utc)
    print(
        f"Prospective funnel pre-persist: deep={len(analyses)}, "
        f"borrowability_ok={collection['borrowability_request_ok']}",
        flush=True,
    )
    status = await persist_standalone_capture(analyses, collection, captured_at=captured_at)
    duration = (datetime.now(timezone.utc) - started).total_seconds()
    print(
        "Prospective funnel complete: "
        f"observed={status['current_run']['observed_snapshots']}, "
        f"inserted={status['current_run']['inserted_snapshots']}, "
        f"distinct_events={status['cumulative']['distinct_sweep_events']}, "
        f"duration={duration:.1f}s",
        flush=True,
    )


async def _bounded_main() -> None:
    await asyncio.wait_for(run(), timeout=MAX_RUNTIME_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(_bounded_main())
    except Exception:
        print("FATAL prospective funnel worker", file=sys.stderr, flush=True)
        raise
