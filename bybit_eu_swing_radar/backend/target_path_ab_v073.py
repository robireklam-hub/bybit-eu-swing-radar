"""Fixed v0.7.3 target-path A/B replay. RESEARCH ONLY.

A keeps the current barrier rule. B changes exactly one thing: a confirmed 15m
pivot stops blocking the target path after a fully closed 15m candle has broken
that pivot before the sweep trigger. Trigger, entry, stop, scores, 1.8R, costs
and execution rules are held fixed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import httpx

from backtest import HistoricalBybitAPI, _ms
from day_worker import (
    DAY_ASSUMED_ROUND_TRIP_COST_BPS,
    DAY_BARRIER_LOOKBACK_15M,
    DAY_BARRIER_MIN_PROMINENCE_ATR,
    DAY_BARRIER_PIVOT_LEFT,
    DAY_BARRIER_PIVOT_RIGHT,
    DAY_MIN_RR,
)
from diagnostics_v073 import (
    DIAGNOSTIC_BASE_COST_BPS,
    DIAGNOSTIC_BASE_HORIZON_HOURS,
    DIAGNOSTIC_SHORT_MODE,
    build_research_candidate,
    evaluate_path,
    gate_snapshot,
)
from diagnostics_v073_perf import fast_scan_sweep_setups
from structure_ab_v073 import (
    STRUCTURE_AB_BLOCK_DAYS,
    STRUCTURE_AB_LOOKBACK_DAYS,
    STRUCTURE_AB_WARMUP_DAYS,
    _build_analysis_cache,
    _empty_counter,
    _json_value,
    _latest_completed_v073_backtest,
    _parse_iso_ms,
    aggregate_trades,
    claim_symbols,
    ensure_schema,
    reset_stale_symbols,
    update_job_counts,
)
from sweep_research import SweepResearchConfig, normalize_bars
from worker import Bar, safe_float

STRATEGY_VERSION = "0.7.3"
TARGET_PATH_AB_JOB_NAME = os.getenv(
    "V073_TARGET_PATH_AB_JOB_NAME", "v073-180d-active-barrier-target-path-ab-v1"
).strip()
DATABASE_URL = os.getenv("DATABASE_URL", "")
TARGET_PATH_AB_ENABLED = os.getenv(
    "V073_TARGET_PATH_AB_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
TARGET_PATH_AB_HTTP_CONCURRENCY = 3
TARGET_PATH_AB_RUN_LOCK_NAME = "trading-radar:day-research:v073-target-path-active-barrier-ab"

GO_MIN_PRIMARY = 300
GO_MIN_SIDE_PRIMARY = 100
GO_MIN_AVG_NET_R = 0.10
GO_MIN_PROFIT_FACTOR = 1.15
GO_MIN_NON_NEGATIVE_BLOCKS = 4
GO_MAX_POSITIVE_BLOCK_CONCENTRATION = 0.50

WARNINGS = [
    "Research-only A/B replay; live v0.7.3 strategy state is never changed.",
    "Exactly one hypothesis: current confirmed 15m barriers versus only still-unbroken confirmed 15m barriers.",
    "Model B marks a pivot stale only after a fully closed 15m candle breaks it before the sweep trigger.",
    "Pivot definition, prominence, lookback, trigger, entry, stop, volume 1.3x, 15m confirmation, scores, 1.8 net-R, costs and execution stay fixed.",
    "The 180-day universe is the completed v0.7.3 backtest universe; survivorship bias remains.",
    "Historical short borrowability is unavailable; shorts remain technical research only.",
    "This 180-day replay is research evidence, not a new untouched validation holdout.",
]


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def active_structural_barrier(
    analysis: Any,
    side: str,
    entry: float,
    trigger_window_start_ms: int,
) -> dict[str, Any] | None:
    """Mirror current 15m pivot barrier logic, excluding already-broken pivots."""
    bars = analysis.bars_15m[-DAY_BARRIER_LOOKBACK_15M:]
    left, right = DAY_BARRIER_PIVOT_LEFT, DAY_BARRIER_PIVOT_RIGHT
    if len(bars) < left + right + 1:
        return None
    interval_ms = 15 * 60 * 1000
    min_prominence = max(analysis.atr_15m * DAY_BARRIER_MIN_PROMINENCE_ATR, 0.0)
    candidates: list[dict[str, Any]] = []

    for index in range(left, len(bars) - right):
        pivot = bars[index]
        confirmed_ms = bars[index + right].start_ms + interval_ms
        if confirmed_ms > trigger_window_start_ms:
            continue
        left_rows = bars[index-left:index]
        right_rows = bars[index+1:index+right+1]

        if side == "long":
            left_ref = max(row.high for row in left_rows)
            right_ref = max(row.high for row in right_rows)
            price = pivot.high
            prominence = min(price-left_ref, price-right_ref)
            valid = (
                price > left_ref and price >= right_ref
                and prominence >= min_prominence and price > entry
            )
            broken = lambda close: close > price
            swing_type = "SWING_HIGH"
        elif side == "short":
            left_ref = min(row.low for row in left_rows)
            right_ref = min(row.low for row in right_rows)
            price = pivot.low
            prominence = min(left_ref-price, right_ref-price)
            valid = (
                price < left_ref and price <= right_ref
                and prominence >= min_prominence and price < entry
            )
            broken = lambda close: close < price
            swing_type = "SWING_LOW"
        else:
            raise ValueError("side must be long or short")
        if not valid:
            continue

        stale = False
        for row in bars[index+right+1:]:
            close_ms = row.start_ms + interval_ms
            if close_ms > trigger_window_start_ms:
                break
            if broken(row.close):
                stale = True
                break
        if stale:
            continue

        candidates.append({
            "price": price,
            "timeframe": "15m",
            "swing_type": swing_type,
            "pivot_start_ms": pivot.start_ms,
            "pivot_time": _iso(pivot.start_ms),
            "confirmed_at": _iso(confirmed_ms),
            "prominence": prominence,
            "prominence_atr": (
                prominence / analysis.atr_15m if analysis.atr_15m > 0 else None
            ),
            "trigger_window_start": _iso(trigger_window_start_ms),
            "active_barrier_rule": "NO_CLOSED_15M_BREAK_BEFORE_TRIGGER",
        })
    if not candidates:
        return None
    return (
        min(candidates, key=lambda item: item["price"])
        if side == "long"
        else max(candidates, key=lambda item: item["price"])
    )


def apply_active_barrier_model(
    candidate: dict[str, Any],
    analysis: Any,
    side: str,
    sweep_event: dict[str, Any],
) -> dict[str, Any]:
    """Clone a baseline candidate and replace only barrier-derived target-path fields."""
    output = copy.deepcopy(candidate)
    entry, stop = safe_float(output.get("entry")), safe_float(output.get("stop"))
    targets = list(output.get("targets") or [])
    sweep_ms = _parse_iso_ms(sweep_event.get("sweep_time"))
    if entry <= 0 or stop <= 0 or len(targets) < 2 or sweep_ms is None:
        return output
    risk = abs(entry-stop)
    if risk <= 0:
        return output

    info = active_structural_barrier(analysis, side, entry, sweep_ms)
    barrier = None if info is None else float(info["price"])
    tp2 = safe_float(targets[1])
    before_tp2 = (
        barrier is not None
        and (entry < barrier < tp2 if side == "long" else tp2 < barrier < entry)
    )
    cost = entry * DAY_ASSUMED_ROUND_TRIP_COST_BPS / 10_000.0
    reference = barrier if before_tp2 else tp2
    expected_rr = max(0.0, (abs(reference-entry)-cost) / risk)
    barrier_net_rr = (
        max(0.0, (abs(barrier-entry)-cost) / risk)
        if barrier is not None else None
    )
    valid = (
        not before_tp2
        or (barrier_net_rr is not None and barrier_net_rr + 1e-9 >= DAY_MIN_RR)
    )
    metrics = dict(output.get("metrics") or {})
    metrics.update({
        "target_path_model": "ACTIVE_UNBROKEN_15M_PIVOT",
        "target_path_valid": valid,
        "nearest_structural_barrier": info,
        "barrier_before_tp2": before_tp2,
        "barrier_net_rr": barrier_net_rr,
    })
    output["metrics"] = metrics
    output["expected_rr"] = float(expected_rr)
    return output


def replay_symbol(
    symbol_meta: dict[str, Any],
    bars_5m: list[Bar],
    btc_bars_5m: list[Bar],
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    symbol = str(symbol_meta["symbol"]).upper()
    empty = {
        "A_CURRENT_BARRIER": {"counters": _empty_counter(), "trades": []},
        "B_ACTIVE_BARRIER": {"counters": _empty_counter(), "trades": []},
    }
    if len(bars_5m) < 500 or len(btc_bars_5m) < 500:
        return {"symbol": symbol, "bars_fetched": len(bars_5m), "models": empty}

    bars_5m = sorted(bars_5m, key=lambda bar: bar.start_ms)
    bar_index = {bar.start_ms: index for index, bar in enumerate(bars_5m)}
    research_bars = normalize_bars(bars_5m)
    bars15, analysis_for = _build_analysis_cache(
        symbol, symbol_meta, bars_5m, btc_bars_5m
    )
    research15 = normalize_bars(bars15)
    config = SweepResearchConfig()
    events: list[dict[str, Any]] = []
    for side in ("long", "short"):
        if side == "short" and DIAGNOSTIC_SHORT_MODE == "disabled":
            continue
        for raw in fast_scan_sweep_setups(
            research_bars, side, bars_15m=research15,
            config=config, include_incomplete=True,
        ):
            event = dict(raw)
            event["side"] = side
            events.append(event)
    events.sort(key=lambda e: (
        _parse_iso_ms(e.get("sweep_time")) or 0,
        str(e.get("side")),
        int(e.get("sweep_index") or 0),
    ))

    models = empty
    last_exit = {
        ("A_CURRENT_BARRIER", "long"): 0,
        ("A_CURRENT_BARRIER", "short"): 0,
        ("B_ACTIVE_BARRIER", "long"): 0,
        ("B_ACTIVE_BARRIER", "short"): 0,
    }
    start_ms, end_ms = _ms(start_at), _ms(end_at)
    horizon = DIAGNOSTIC_BASE_HORIZON_HOURS * 12
    short_proxy = bool(symbol_meta.get("current_shortable_proxy"))
    stale_removed = recovered = 0

    for event in events:
        sweep_ms = _parse_iso_ms(event.get("sweep_time"))
        structure_ms = _parse_iso_ms(event.get("structure_shift_time_5m"))
        if (
            sweep_ms is None or structure_ms is None
            or sweep_ms < start_ms or sweep_ms >= end_ms
        ):
            continue
        confirm_index = bar_index.get(structure_ms)
        if confirm_index is None or confirm_index + horizon >= len(bars_5m):
            continue
        opened_ms = structure_ms + 5 * 60 * 1000
        if opened_ms < start_ms or opened_ms >= end_ms:
            continue
        cached = analysis_for(confirm_index)
        if cached is None:
            continue
        analysis = copy.copy(cached[0])
        side = str(event["side"])
        analysis.shortable = (
            False if side == "long"
            else DIAGNOSTIC_SHORT_MODE == "technical_only"
            or (DIAGNOSTIC_SHORT_MODE == "current_proxy" and short_proxy)
        )

        a = build_research_candidate(analysis, side, event)
        b = None if a is None else apply_active_barrier_model(a, analysis, side, event)
        candidates = {"A_CURRENT_BARRIER": a, "B_ACTIVE_BARRIER": b}
        for name, candidate in candidates.items():
            if candidate is not None:
                models[name]["counters"]["candidates"] += 1

        if a is not None and b is not None:
            am, bm = a.get("metrics") or {}, b.get("metrics") or {}
            ab, bb = am.get("nearest_structural_barrier"), bm.get("nearest_structural_barrier")
            if ab is not None and (
                bb is None or safe_float(ab.get("price")) != safe_float(bb.get("price"))
            ):
                stale_removed += 1
            if not bool(am.get("target_path_valid")) and bool(bm.get("target_path_valid")):
                recovered += 1

        if a is None:
            continue
        targets = list(a.get("targets") or [])
        if len(targets) < 3:
            continue
        entry, stop = safe_float(a.get("entry")), safe_float(a.get("stop"))
        future = bars_5m[confirm_index+1:min(len(bars_5m), confirm_index+1+horizon)]
        path = evaluate_path(
            side, entry, stop,
            safe_float(targets[0]), safe_float(targets[1]), safe_float(targets[2]),
            future,
        )
        risk = abs(entry-stop)
        if path is None or risk <= 0:
            continue
        closed_ms = _ms(path["closed_at"])
        cost_r = (entry * DIAGNOSTIC_BASE_COST_BPS / 10_000.0) / risk
        net_r = float(path["gross_r"]) - cost_r
        block = int(
            (datetime.fromtimestamp(opened_ms/1000, tz=timezone.utc)-start_at)
            .total_seconds() // (STRUCTURE_AB_BLOCK_DAYS * 86_400)
        )
        if block < 0 or block >= STRUCTURE_AB_LOOKBACK_DAYS // STRUCTURE_AB_BLOCK_DAYS:
            continue

        for name, candidate in candidates.items():
            gates = gate_snapshot(candidate, side, event, short_proxy)
            if gates.get("pass_strict_trade"):
                models[name]["counters"]["strict_trade_raw"] += 1
            if candidate is None or not gates.get("pass_strict_trade"):
                continue
            key = (name, side)
            if opened_ms < last_exit[key]:
                continue
            last_exit[key] = closed_ms
            models[name]["counters"]["primary_strict_trades"] += 1
            models[name]["trades"].append({
                "symbol": symbol,
                "side": side,
                "opened_at": datetime.fromtimestamp(
                    opened_ms/1000, tz=timezone.utc
                ).isoformat(),
                "closed_at": path["closed_at"].isoformat(),
                "block_index": block,
                "entry": entry,
                "stop": stop,
                "net_r": round(net_r, 6),
                "gross_r": float(path["gross_r"]),
                "mfe_r": float(path["mfe_r"]),
                "mae_r": float(path["mae_r"]),
                "exit_reason": path["exit_reason"],
            })
    return {
        "symbol": symbol,
        "bars_fetched": len(bars_5m),
        "models": models,
        "diagnostics": {
            "stale_barriers_removed_by_b": stale_removed,
            "target_paths_recovered_by_b": recovered,
        },
    }


def build_report_from_symbol_results(
    results: list[dict[str, Any]],
    start_at: datetime,
    end_at: datetime,
    *,
    expected_symbols: int | None = None,
) -> dict[str, Any]:
    block_count = STRUCTURE_AB_LOOKBACK_DAYS // STRUCTURE_AB_BLOCK_DAYS
    reports: dict[str, Any] = {}
    for name in ("A_CURRENT_BARRIER", "B_ACTIVE_BARRIER"):
        trades = [
            trade for result in results
            for trade in ((result.get("models") or {}).get(name) or {}).get("trades", [])
        ]
        by_side = {
            side: aggregate_trades([t for t in trades if t.get("side") == side])
            for side in ("long", "short")
        }
        blocks = []
        for index in range(block_count):
            row = aggregate_trades(
                [t for t in trades if int(t.get("block_index", -1)) == index]
            )
            row["index"] = index
            blocks.append(row)
        positive = [max(float(b.get("total_net_r") or 0.0), 0.0) for b in blocks]
        concentration = max(positive) / sum(positive) if sum(positive) > 0 else None
        reports[name] = {
            "overall": aggregate_trades(trades),
            "by_side": by_side,
            "blocks_30d": blocks,
            "non_negative_blocks": sum(
                1 for b in blocks
                if int(b["sample_size"]) > 0 and float(b["total_net_r"]) >= 0
            ),
            "positive_block_concentration": (
                round(concentration, 6) if concentration is not None else None
            ),
        }

    a, b = reports["A_CURRENT_BARRIER"], reports["B_ACTIVE_BARRIER"]
    ao, bo = a["overall"], b["overall"]
    pf, concentration = bo.get("profit_factor"), b["positive_block_concentration"]
    production_checks = {
        "all_symbols_completed": (
            True if expected_symbols is None else len(results) == expected_symbols
        ),
        "primary_sample_gte_300": int(bo["sample_size"]) >= GO_MIN_PRIMARY,
        "long_sample_gte_100": int(b["by_side"]["long"]["sample_size"]) >= GO_MIN_SIDE_PRIMARY,
        "short_sample_gte_100": int(b["by_side"]["short"]["sample_size"]) >= GO_MIN_SIDE_PRIMARY,
        "average_net_r_gt_0_10": (
            bo["average_net_r"] is not None
            and float(bo["average_net_r"]) > GO_MIN_AVG_NET_R
        ),
        "profit_factor_gte_1_15": (
            bool(bo.get("profit_factor_unbounded"))
            or (pf is not None and float(pf) >= GO_MIN_PROFIT_FACTOR)
        ),
        "non_negative_blocks_gte_4_of_6": b["non_negative_blocks"] >= GO_MIN_NON_NEGATIVE_BLOCKS,
        "positive_block_concentration_lte_0_50": (
            concentration is not None and concentration <= GO_MAX_POSITIVE_BLOCK_CONCENTRATION
        ),
    }
    a_avg, b_avg = ao.get("average_net_r"), bo.get("average_net_r")
    a_pf, b_pf = ao.get("profit_factor"), bo.get("profit_factor")
    hypothesis_checks = {
        "b_sample_not_lower": int(bo["sample_size"]) >= int(ao["sample_size"]),
        "b_recovers_at_least_one_primary_trade": int(bo["sample_size"]) > int(ao["sample_size"]),
        "b_average_net_r_not_lower": (
            a_avg is not None and b_avg is not None and float(b_avg) >= float(a_avg)
        ),
        "b_profit_factor_not_lower": (
            bool(bo.get("profit_factor_unbounded"))
            or (
                a_pf is not None and b_pf is not None
                and float(b_pf) >= float(a_pf)
            )
        ),
    }
    stale = sum(int((r.get("diagnostics") or {}).get("stale_barriers_removed_by_b") or 0) for r in results)
    recovered = sum(int((r.get("diagnostics") or {}).get("target_paths_recovered_by_b") or 0) for r in results)
    return {
        "strategy_version": STRATEGY_VERSION,
        "research_only": True,
        "live_strategy_mutated": False,
        "hypothesis": (
            "A confirmed 15m pivot should stop blocking the target path after a "
            "fully closed 15m candle broke it before the sweep trigger."
        ),
        "window": {
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "lookback_days": STRUCTURE_AB_LOOKBACK_DAYS,
            "block_days": STRUCTURE_AB_BLOCK_DAYS,
            "blocks": block_count,
        },
        "models": reports,
        "ab_delta": {
            "primary_sample": int(bo["sample_size"]) - int(ao["sample_size"]),
            "average_net_r": (
                None if a_avg is None or b_avg is None
                else round(float(b_avg)-float(a_avg), 6)
            ),
            "profit_factor": (
                None if a_pf is None or b_pf is None
                else round(float(b_pf)-float(a_pf), 6)
            ),
            "stale_barriers_removed": stale,
            "target_paths_recovered": recovered,
        },
        "hypothesis_criteria": {
            "fixed_before_run": True,
            "checks": hypothesis_checks,
            "decision": "SUPPORTED" if all(hypothesis_checks.values()) else "NOT_SUPPORTED",
        },
        "production_criteria": {
            "fixed_before_run": True,
            "checks": production_checks,
            "decision": "GO" if all(production_checks.values()) else "NO_GO",
        },
        "validation_policy": {
            "untouched_forward_holdout_required_before_promotion": True,
            "warning": (
                "A supported hypothesis is not production-promotable from this "
                "research replay alone; a new untouched/forward holdout is required."
            ),
        },
    }


def job_parameters(source_id: int) -> dict[str, Any]:
    return {
        "source_backtest_job_id": source_id,
        "lookback_days": STRUCTURE_AB_LOOKBACK_DAYS,
        "warmup_days": STRUCTURE_AB_WARMUP_DAYS,
        "block_days": STRUCTURE_AB_BLOCK_DAYS,
        "model_a": "CURRENT_CONFIRMED_15M_PIVOT_BARRIER",
        "model_b": "ONLY_UNBROKEN_CONFIRMED_15M_PIVOT_BARRIER",
        "barrier_lookback_15m": DAY_BARRIER_LOOKBACK_15M,
        "pivot_left": DAY_BARRIER_PIVOT_LEFT,
        "pivot_right": DAY_BARRIER_PIVOT_RIGHT,
        "min_prominence_atr": DAY_BARRIER_MIN_PROMINENCE_ATR,
        "net_rr": DAY_MIN_RR,
        "cost_bps": DIAGNOSTIC_BASE_COST_BPS,
        "horizon_hours": DIAGNOSTIC_BASE_HORIZON_HOURS,
        "short_mode": DIAGNOSTIC_SHORT_MODE,
    }


async def create_job_if_needed(conn: asyncpg.Connection) -> dict[str, Any]:
    existing = await conn.fetchrow(
        """
        SELECT * FROM day_trade_structure_ab_jobs
        WHERE job_name=$1 AND strategy_version=$2
        ORDER BY id DESC LIMIT 1
        """,
        TARGET_PATH_AB_JOB_NAME, STRATEGY_VERSION,
    )
    if existing:
        return dict(existing)
    source = await _latest_completed_v073_backtest(conn)
    if source is None:
        raise RuntimeError("No completed v0.7.3 backtest exists")
    universe = _json_value(source.get("universe"), [])
    if not universe:
        raise RuntimeError("Completed v0.7.3 backtest universe is empty")
    end_at = source["end_at"]
    start_at = end_at - timedelta(days=STRUCTURE_AB_LOOKBACK_DAYS)
    warmup = start_at - timedelta(days=STRUCTURE_AB_WARMUP_DAYS)
    params = job_parameters(int(source["id"]))
    key = hashlib.sha256(
        (
            f"{STRATEGY_VERSION}|{TARGET_PATH_AB_JOB_NAME}|{source['id']}|"
            f"{start_at.isoformat()}|{end_at.isoformat()}|"
            f"{json.dumps(params, sort_keys=True)}"
        ).encode()
    ).hexdigest()
    row = await conn.fetchrow(
        """
        INSERT INTO day_trade_structure_ab_jobs (
            job_key,job_name,strategy_version,source_backtest_job_id,status,
            start_at,end_at,warmup_start_at,parameters,universe,warnings,total_symbols
        ) VALUES (
            $1,$2,$3,$4,'PENDING',$5,$6,$7,$8::jsonb,$9::jsonb,$10::jsonb,$11
        ) RETURNING *
        """,
        key, TARGET_PATH_AB_JOB_NAME, STRATEGY_VERSION, int(source["id"]),
        start_at, end_at, warmup, json.dumps(params), json.dumps(universe),
        json.dumps(WARNINGS), len(universe),
    )
    job = dict(row)
    await conn.executemany(
        """
        INSERT INTO day_trade_structure_ab_symbols (job_id,symbol,status,metadata)
        VALUES ($1,$2,'PENDING',$3::jsonb)
        ON CONFLICT (job_id,symbol) DO NOTHING
        """,
        [(int(job["id"]), str(item["symbol"]), json.dumps(item)) for item in universe],
    )
    return job


async def run_target_path_ab_batch() -> dict[str, Any]:
    if not TARGET_PATH_AB_ENABLED:
        return {"enabled": False, "status": "DISABLED"}
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    timeout = httpx.Timeout(45.0, connect=15.0)
    async with httpx.AsyncClient(
        timeout=timeout, limits=httpx.Limits(max_connections=TARGET_PATH_AB_HTTP_CONCURRENCY)
    ) as client:
        api = HistoricalBybitAPI(client)
        conn = await asyncpg.connect(DATABASE_URL, timeout=30)
        locked = False
        try:
            row = await conn.fetchrow(
                "SELECT pg_try_advisory_lock(hashtext($1)) AS acquired",
                TARGET_PATH_AB_RUN_LOCK_NAME,
            )
            locked = bool(row["acquired"])
            if not locked:
                return {"enabled": True, "status": "SKIPPED_ALREADY_RUNNING"}
            await ensure_schema(conn)
            job = await create_job_if_needed(conn)
            job_id = int(job["id"])
            if str(job["status"]) in {"COMPLETED", "PARTIAL", "FAILED"}:
                return {"enabled": True, "job_id": job_id, **(await update_job_counts(conn, job_id))}
            await conn.execute(
                """
                UPDATE day_trade_structure_ab_jobs
                SET status='RUNNING',started_at=COALESCE(started_at,NOW()),
                    last_run_at=NOW(),updated_at=NOW() WHERE id=$1
                """,
                job_id,
            )
            await reset_stale_symbols(conn, job_id)
            claimed = await claim_symbols(conn, job_id)
            if not claimed:
                return {"enabled": True, "job_id": job_id, **(await update_job_counts(conn, job_id))}

            btc = await api.klines_range(
                "BTCUSDC", _ms(job["warmup_start_at"]), _ms(job["end_at"])
            )
            processed = []
            for item in claimed:
                sid, symbol = int(item["id"]), str(item["symbol"])
                meta = _json_value(item.get("metadata"), {})
                try:
                    bars = btc if symbol == "BTCUSDC" else await api.klines_range(
                        symbol, _ms(job["warmup_start_at"]), _ms(job["end_at"])
                    )
                    result = replay_symbol(meta, bars, btc, job["start_at"], job["end_at"])
                    await conn.execute(
                        """
                        UPDATE day_trade_structure_ab_symbols
                        SET status='COMPLETED',bars_fetched=$2,result=$3::jsonb,
                            completed_at=NOW(),last_error=NULL WHERE id=$1
                        """,
                        sid, len(bars), json.dumps(result),
                    )
                    processed.append({"symbol": symbol, "status": "COMPLETED", "bars": len(bars)})
                except Exception as exc:
                    await conn.execute(
                        """
                        UPDATE day_trade_structure_ab_symbols
                        SET status='FAILED',completed_at=NOW(),last_error=$2 WHERE id=$1
                        """,
                        sid, f"{type(exc).__name__}: {exc}"[:4000],
                    )
                    processed.append({"symbol": symbol, "status": "FAILED", "error": str(exc)})
            return {
                "enabled": True,
                "job_id": job_id,
                "job_name": TARGET_PATH_AB_JOB_NAME,
                "processed": processed,
                **(await update_job_counts(conn, job_id)),
            }
        finally:
            if locked:
                try:
                    await conn.execute(
                        "SELECT pg_advisory_unlock(hashtext($1))",
                        TARGET_PATH_AB_RUN_LOCK_NAME,
                    )
                except Exception:
                    pass
            await conn.close()
