"""Railway/DB batch orchestration for target-path A/B/C research."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

import asyncpg
import httpx

from backtest import HistoricalBybitAPI, _ms
from diagnostics_v073 import (
    DIAGNOSTIC_BASE_COST_BPS,
    DIAGNOSTIC_BASE_HORIZON_HOURS,
    DIAGNOSTIC_SHORT_MODE,
)
from structure_ab_v073 import (
    STRUCTURE_AB_BLOCK_DAYS,
    STRUCTURE_AB_LOOKBACK_DAYS,
    STRUCTURE_AB_WARMUP_DAYS,
    _json_value,
    _latest_completed_v073_backtest,
    claim_symbols,
    ensure_schema,
    reset_stale_symbols,
    update_job_counts,
)
from target_path_ab_core_v073 import (
    DATABASE_URL,
    DAY_BARRIER_LOOKBACK_15M,
    DAY_BARRIER_MIN_PROMINENCE_ATR,
    DAY_BARRIER_PIVOT_LEFT,
    DAY_BARRIER_PIVOT_RIGHT,
    DAY_MIN_RR,
    DAY_TRIGGER_VOLUME_RATIO,
    GO_MAX_POSITIVE_BLOCK_CONCENTRATION,
    GO_MIN_AVG_NET_R,
    GO_MIN_NON_NEGATIVE_BLOCKS,
    GO_MIN_PRIMARY,
    GO_MIN_PROFIT_FACTOR,
    GO_MIN_SIDE_PRIMARY,
    MODEL_CURRENT,
    MODEL_FRESH,
    MODEL_IGNORE,
    STRATEGY_VERSION,
    TARGET_PATH_AB_ENABLED,
    TARGET_PATH_AB_JOB_NAME,
    TARGET_PATH_AB_RUN_LOCK_NAME,
    WARNINGS,
)
from target_path_ab_replay_v073 import replay_symbol

def job_parameters(source_id: int) -> dict[str, Any]:
    return {
        "source_backtest_job_id": source_id,
        "lookback_days": STRUCTURE_AB_LOOKBACK_DAYS,
        "warmup_days": STRUCTURE_AB_WARMUP_DAYS,
        "block_days": STRUCTURE_AB_BLOCK_DAYS,
        "model_a": "CURRENT_STRUCTURAL_TARGET_PATH",
        "model_b": "FRESH_UNCONSUMED_BY_CLOSED_15M_CLOSE",
        "model_c": "IGNORE_STRUCTURAL_TARGET_PATH_DIAGNOSTIC_ONLY",
        "freshness_rule": {
            "timeframe": "15m",
            "consume_long": "closed_15m_close > swing_high",
            "consume_short": "closed_15m_close < swing_low",
            "wick_or_touch_consumes": False,
            "check_window": "after pivot confirmation through the actual 5m trade trigger",
            "pivot_must_be_confirmed_before": "trigger-window start / sweep",
        },
        "barrier_pivot_left": DAY_BARRIER_PIVOT_LEFT,
        "barrier_pivot_right": DAY_BARRIER_PIVOT_RIGHT,
        "barrier_lookback_15m": DAY_BARRIER_LOOKBACK_15M,
        "barrier_min_prominence_atr": DAY_BARRIER_MIN_PROMINENCE_ATR,
        "volume_ratio": DAY_TRIGGER_VOLUME_RATIO,
        "net_rr": DAY_MIN_RR,
        "horizon_hours": DIAGNOSTIC_BASE_HORIZON_HOURS,
        "cost_bps": DIAGNOSTIC_BASE_COST_BPS,
        "short_mode": DIAGNOSTIC_SHORT_MODE,
        "go_thresholds": {
            "primary_sample": GO_MIN_PRIMARY,
            "min_each_side": GO_MIN_SIDE_PRIMARY,
            "avg_net_r": GO_MIN_AVG_NET_R,
            "profit_factor": GO_MIN_PROFIT_FACTOR,
            "non_negative_blocks": GO_MIN_NON_NEGATIVE_BLOCKS,
            "positive_block_concentration": GO_MAX_POSITIVE_BLOCK_CONCENTRATION,
            "must_beat_current_average_net_r": True,
            "must_not_reduce_current_profit_factor": True,
        },
    }


async def create_job_if_needed(connection: asyncpg.Connection) -> dict[str, Any]:
    existing = await connection.fetchrow(
        """
        SELECT * FROM day_trade_structure_ab_jobs
        WHERE job_name=$1 AND strategy_version=$2
        ORDER BY id DESC LIMIT 1
        """,
        TARGET_PATH_AB_JOB_NAME,
        STRATEGY_VERSION,
    )
    if existing:
        return dict(existing)

    source = await _latest_completed_v073_backtest(connection)
    if source is None:
        raise RuntimeError("No completed v0.7.3 backtest exists")
    universe = _json_value(source.get("universe"), [])
    if not universe:
        raise RuntimeError("Completed v0.7.3 backtest universe is empty")
    end_at = source["end_at"]
    start_at = end_at - timedelta(days=STRUCTURE_AB_LOOKBACK_DAYS)
    warmup_start = start_at - timedelta(days=STRUCTURE_AB_WARMUP_DAYS)
    params = job_parameters(int(source["id"]))
    raw_key = (
        f"{STRATEGY_VERSION}|{TARGET_PATH_AB_JOB_NAME}|{source['id']}|"
        f"{start_at.isoformat()}|{end_at.isoformat()}|"
        f"{json.dumps(params, sort_keys=True)}"
    )
    job_key = hashlib.sha256(raw_key.encode()).hexdigest()
    row = await connection.fetchrow(
        """
        INSERT INTO day_trade_structure_ab_jobs (
            job_key,job_name,strategy_version,source_backtest_job_id,status,
            start_at,end_at,warmup_start_at,parameters,universe,warnings,total_symbols
        ) VALUES (
            $1,$2,$3,$4,'PENDING',$5,$6,$7,$8::jsonb,$9::jsonb,$10::jsonb,$11
        ) RETURNING *
        """,
        job_key,
        TARGET_PATH_AB_JOB_NAME,
        STRATEGY_VERSION,
        int(source["id"]),
        start_at,
        end_at,
        warmup_start,
        json.dumps(params),
        json.dumps(universe),
        json.dumps(WARNINGS),
        len(universe),
    )
    job = dict(row)
    await connection.executemany(
        """
        INSERT INTO day_trade_structure_ab_symbols (job_id,symbol,status,metadata)
        VALUES ($1,$2,'PENDING',$3::jsonb)
        ON CONFLICT (job_id,symbol) DO NOTHING
        """,
        [
            (int(job["id"]), str(item["symbol"]), json.dumps(item))
            for item in universe
        ],
    )
    return job


async def run_target_path_ab_batch() -> dict[str, Any]:
    if not TARGET_PATH_AB_ENABLED:
        return {"enabled": False, "status": "DISABLED"}
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    timeout = httpx.Timeout(45.0, connect=15.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=httpx.Limits(max_connections=3),
    ) as client:
        api = HistoricalBybitAPI(client)
        connection = await asyncpg.connect(DATABASE_URL, timeout=30)
        lock_acquired = False
        try:
            lock_row = await connection.fetchrow(
                "SELECT pg_try_advisory_lock(hashtext($1)) AS acquired",
                TARGET_PATH_AB_RUN_LOCK_NAME,
            )
            lock_acquired = bool(lock_row["acquired"])
            if not lock_acquired:
                return {
                    "enabled": True,
                    "job_name": TARGET_PATH_AB_JOB_NAME,
                    "status": "SKIPPED_ALREADY_RUNNING",
                }
            await ensure_schema(connection)
            job = await create_job_if_needed(connection)
            job_id = int(job["id"])
            if str(job["status"]) in {"COMPLETED", "PARTIAL", "FAILED"}:
                return {
                    "enabled": True,
                    "job_id": job_id,
                    "job_name": TARGET_PATH_AB_JOB_NAME,
                    **(await update_job_counts(connection, job_id)),
                }

            await connection.execute(
                """
                UPDATE day_trade_structure_ab_jobs
                SET status='RUNNING',started_at=COALESCE(started_at,NOW()),
                    last_run_at=NOW(),updated_at=NOW()
                WHERE id=$1
                """,
                job_id,
            )
            await reset_stale_symbols(connection, job_id)
            claimed = await claim_symbols(connection, job_id)
            if not claimed:
                return {
                    "enabled": True,
                    "job_id": job_id,
                    "job_name": TARGET_PATH_AB_JOB_NAME,
                    **(await update_job_counts(connection, job_id)),
                }

            warmup_start = job["warmup_start_at"]
            end_at = job["end_at"]
            start_at = job["start_at"]
            btc_bars = await api.klines_range(
                "BTCUSDC", _ms(warmup_start), _ms(end_at)
            )
            batch_results: list[dict[str, Any]] = []
            for row in claimed:
                symbol_id = int(row["id"])
                symbol = str(row["symbol"])
                metadata = _json_value(row.get("metadata"), {})
                try:
                    bars = (
                        btc_bars
                        if symbol == "BTCUSDC"
                        else await api.klines_range(
                            symbol, _ms(warmup_start), _ms(end_at)
                        )
                    )
                    result = replay_symbol(metadata, bars, btc_bars, start_at, end_at)
                    await connection.execute(
                        """
                        UPDATE day_trade_structure_ab_symbols
                        SET status='COMPLETED',bars_fetched=$2,result=$3::jsonb,
                            completed_at=NOW(),last_error=NULL
                        WHERE id=$1
                        """,
                        symbol_id,
                        len(bars),
                        json.dumps(result),
                    )
                    batch_results.append(
                        {
                            "symbol": symbol,
                            "status": "COMPLETED",
                            "bars": len(bars),
                            "current_primary": int(result["models"][MODEL_CURRENT]["counters"]["primary_strict_trades"]),
                            "fresh_primary": int(result["models"][MODEL_FRESH]["counters"]["primary_strict_trades"]),
                            "ignore_primary": int(result["models"][MODEL_IGNORE]["counters"]["primary_strict_trades"]),
                        }
                    )
                except Exception as exc:
                    await connection.execute(
                        """
                        UPDATE day_trade_structure_ab_symbols
                        SET status='FAILED',completed_at=NOW(),last_error=$2
                        WHERE id=$1
                        """,
                        symbol_id,
                        f"{type(exc).__name__}: {exc}"[:4000],
                    )
                    batch_results.append(
                        {
                            "symbol": symbol,
                            "status": "FAILED",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

            return {
                "enabled": True,
                "job_id": job_id,
                "job_name": TARGET_PATH_AB_JOB_NAME,
                "processed": batch_results,
                **(await update_job_counts(connection, job_id)),
            }
        finally:
            if lock_acquired:
                try:
                    await connection.execute(
                        "SELECT pg_advisory_unlock(hashtext($1))",
                        TARGET_PATH_AB_RUN_LOCK_NAME,
                    )
                except Exception:
                    pass
            await connection.close()
