"""Materialized opportunity-level research dataset for Trading Radar v0.7.3.

Research only. This module reuses the existing v0.7.3 diagnostic replay and
stores only evaluable 5m structure-shift opportunities. Live strategy state,
scoring, triggers, execution and eligibility are never mutated.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import httpx

from backtest import HistoricalBybitAPI, _ms
from diagnostics_v073 import (
    DIAGNOSTIC_BATCH_SYMBOLS,
    DIAGNOSTIC_HTTP_CONCURRENCY,
    DIAGNOSTIC_SHORT_MODE,
    STRATEGY_VERSION,
    _latest_completed_v073_backtest,
    claim_symbols,
    ensure_schema,
    insert_events,
    replay_diagnostic_symbol,
    reset_stale_symbols,
    update_job_counts,
)

DATASET_VERSION = "day-trade-research-v1"
JOB_NAME = "v073-180d-day-trade-research-dataset-v1"
LOOKBACK_DAYS = 180
WARMUP_DAYS = 14
DISCOVERY_DAYS = 120
VALIDATION_DAYS = 60
BLOCK_DAYS = 30
RUN_LOCK_NAME = "trading-radar:day-research:v073-dataset-v1"
DATABASE_URL = os.getenv("DATABASE_URL", "")

PROFILE_NUMERIC_FEATURES = (
    "sweep_depth_atr",
    "bars_from_sweep_to_confirmation",
    "volume_ratio_5m",
    "turnover_24h_usdc",
    "modeled_spread_bps",
    "expansion_score",
    "side_direction_score",
    "quality_score",
    "setup_score",
    "expected_rr",
)
PROFILE_CATEGORICAL_FEATURES = (
    "side",
    "btc_volatility_regime",
    "btc_structure_1h",
    "btc_structure_4h",
    "timeframe_conflict",
)

WARNINGS = [
    "Research-only materialized dataset; live v0.7.3 strategy is never changed.",
    "Opportunity population is evaluable 5m structure-shift events, not only STRICT trades.",
    "Volume, score, 15m structure, target-path and RR states are retained as features; they do not filter dataset membership.",
    "Discovery is the first 120 days (four 30-day blocks); validation is the final 60 days (two 30-day blocks).",
    "Quartile cut points are learned on discovery only and then frozen for validation.",
    "Profiles are exploratory evidence, not promotion decisions; multiple-testing/overfitting controls are required before strategy changes.",
    "Historical spread is modelled from rolling turnover; historical short borrowability is unavailable and shorts remain technical research only.",
    "Historical aligned OI/funding is not injected into v1; derivatives remain context-only and never a hard gate.",
    "The source universe is the latest completed v0.7.3 USDC backtest universe, so survivorship bias remains.",
]

def job_parameters(source_id: int) -> dict[str, Any]:
    return {
        "dataset_version": DATASET_VERSION,
        "source_backtest_job_id": source_id,
        "lookback_days": LOOKBACK_DAYS,
        "warmup_days": WARMUP_DAYS,
        "discovery_days": DISCOVERY_DAYS,
        "validation_days": VALIDATION_DAYS,
        "block_days": BLOCK_DAYS,
        "batch_symbols": DIAGNOSTIC_BATCH_SYMBOLS,
        "short_mode": DIAGNOSTIC_SHORT_MODE,
        "membership": "candidate_built AND pass_structure_5m",
        "hard_gate_filtering": False,
        "base_outcome": "8h net R after 20 bps using existing entry/stop/TP2 path",
        "profile_numeric_features": list(PROFILE_NUMERIC_FEATURES),
        "profile_categorical_features": list(PROFILE_CATEGORICAL_FEATURES),
    }

def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value

async def create_job_if_needed(connection: asyncpg.Connection) -> dict[str, Any]:
    existing = await connection.fetchrow(
        """
        SELECT * FROM day_trade_diagnostic_jobs
        WHERE job_name=$1 AND strategy_version=$2
        ORDER BY id DESC LIMIT 1
        """,
        JOB_NAME,
        STRATEGY_VERSION,
    )
    if existing:
        return dict(existing)

    source = await _latest_completed_v073_backtest(connection)
    if source is None:
        raise RuntimeError("No completed v0.7.3 backtest exists")
    universe = list(_json_value(source.get("universe"), []))
    if not universe:
        raise RuntimeError("Completed v0.7.3 backtest universe is empty")

    source_id = int(source["id"])
    end_at = source["end_at"]
    start_at = end_at - timedelta(days=LOOKBACK_DAYS)
    warmup_start = start_at - timedelta(days=WARMUP_DAYS)
    development_end = start_at + timedelta(days=DISCOVERY_DAYS)
    params = job_parameters(source_id)
    raw_key = (
        f"{STRATEGY_VERSION}|{JOB_NAME}|{source_id}|"
        f"{start_at.isoformat()}|{end_at.isoformat()}|"
        f"{json.dumps(params, sort_keys=True)}"
    )
    job_key = hashlib.sha256(raw_key.encode()).hexdigest()
    row = await connection.fetchrow(
        """
        INSERT INTO day_trade_diagnostic_jobs (
            job_key,job_name,strategy_version,source_backtest_job_id,status,
            start_at,end_at,warmup_start_at,development_end_at,parameters,
            universe,warnings,total_symbols
        ) VALUES (
            $1,$2,$3,$4,'PENDING',$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11::jsonb,$12
        ) RETURNING *
        """,
        job_key,
        JOB_NAME,
        STRATEGY_VERSION,
        source_id,
        start_at,
        end_at,
        warmup_start,
        development_end,
        json.dumps(params),
        json.dumps(universe),
        json.dumps(WARNINGS),
        len(universe),
    )
    job = dict(row)
    await connection.executemany(
        """
        INSERT INTO day_trade_diagnostic_symbols (job_id,symbol,status,metadata)
        VALUES ($1,$2,'PENDING',$3::jsonb)
        ON CONFLICT (job_id,symbol) DO NOTHING
        """,
        [(int(job["id"]), str(item["symbol"]), json.dumps(item)) for item in universe],
    )
    return job

async def run_dataset_batch() -> dict[str, Any]:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    timeout = httpx.Timeout(45.0, connect=15.0)
    limits = httpx.Limits(max_connections=DIAGNOSTIC_HTTP_CONCURRENCY)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        api = HistoricalBybitAPI(client)
        connection = await asyncpg.connect(DATABASE_URL, timeout=30)
        lock_acquired = False
        try:
            lock = await connection.fetchrow(
                "SELECT pg_try_advisory_lock(hashtext($1)) AS acquired",
                RUN_LOCK_NAME,
            )
            lock_acquired = bool(lock["acquired"])
            if not lock_acquired:
                return {"job_name": JOB_NAME, "status": "SKIPPED_ALREADY_RUNNING"}

            await ensure_schema(connection)
            job = await create_job_if_needed(connection)
            job_id = int(job["id"])
            if str(job["status"]) in {"COMPLETED", "PARTIAL", "FAILED"}:
                return {"job_id": job_id, "job_name": JOB_NAME, **(await update_job_counts(connection, job_id))}

            await connection.execute(
                """
                UPDATE day_trade_diagnostic_jobs
                SET status='RUNNING',started_at=COALESCE(started_at,NOW()),
                    last_run_at=NOW(),updated_at=NOW()
                WHERE id=$1
                """,
                job_id,
            )
            await reset_stale_symbols(connection, job_id)
            claimed = await claim_symbols(connection, job_id)
            if not claimed:
                return {"job_id": job_id, "job_name": JOB_NAME, **(await update_job_counts(connection, job_id))}

            warmup_start = job["warmup_start_at"]
            start_at = job["start_at"]
            end_at = job["end_at"]
            development_end = job["development_end_at"]
            btc_bars = await api.klines_range("BTCUSDC", _ms(warmup_start), _ms(end_at))
            processed: list[dict[str, Any]] = []

            for row in claimed:
                symbol_id = int(row["id"])
                symbol = str(row["symbol"])
                metadata = _json_value(row.get("metadata"), {})
                try:
                    bars = (
                        btc_bars
                        if symbol == "BTCUSDC"
                        else await api.klines_range(symbol, _ms(warmup_start), _ms(end_at))
                    )
                    replay = replay_diagnostic_symbol(
                        job_id,
                        metadata,
                        bars,
                        btc_bars,
                        start_at,
                        end_at,
                        development_end,
                    )
                    opportunities = [
                        event
                        for event in replay.events
                        if event.get("candidate_built") and event.get("pass_structure_5m")
                    ]
                    async with connection.transaction():
                        inserted = await insert_events(connection, opportunities)
                        stored = await connection.fetchrow(
                            """
                            SELECT COUNT(*) AS total,
                                   COUNT(*) FILTER (WHERE base_net_r IS NOT NULL) AS evaluable,
                                   COUNT(*) FILTER (WHERE dataset_split='DEVELOPMENT') AS discovery,
                                   COUNT(*) FILTER (WHERE dataset_split='VALIDATION') AS validation
                            FROM day_trade_diagnostic_events
                            WHERE job_id=$1 AND symbol=$2
                            """,
                            job_id,
                            symbol,
                        )
                        await connection.execute(
                            """
                            UPDATE day_trade_diagnostic_symbols
                            SET status='COMPLETED',bars_fetched=$2,evaluation_bars=$3,
                                event_count=$4,primary_event_count=$5,
                                strict_eligible_count=0,strict_trade_count=0,
                                completed_at=NOW(),last_error=NULL
                            WHERE id=$1
                            """,
                            symbol_id,
                            replay.bars_fetched,
                            replay.evaluation_bars,
                            int(stored["total"] or 0),
                            int(stored["evaluable"] or 0),
                        )
                    processed.append(
                        {
                            "symbol": symbol,
                            "status": "COMPLETED",
                            "bars": replay.bars_fetched,
                            "opportunities_inserted_this_run": inserted,
                            "opportunities_stored": int(stored["total"] or 0),
                            "outcome_evaluable": int(stored["evaluable"] or 0),
                            "discovery": int(stored["discovery"] or 0),
                            "validation": int(stored["validation"] or 0),
                        }
                    )
                except Exception as exc:
                    await connection.execute(
                        """
                        UPDATE day_trade_diagnostic_symbols
                        SET status='FAILED',completed_at=NOW(),last_error=$2
                        WHERE id=$1
                        """,
                        symbol_id,
                        f"{type(exc).__name__}: {exc}"[:4000],
                    )
                    processed.append(
                        {"symbol": symbol, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
                    )

            return {
                "research_only": True,
                "live_strategy_mutated": False,
                "dataset_version": DATASET_VERSION,
                "job_id": job_id,
                "job_name": JOB_NAME,
                "processed": processed,
                **(await update_job_counts(connection, job_id)),
            }
        finally:
            if lock_acquired:
                try:
                    await connection.execute(
                        "SELECT pg_advisory_unlock(hashtext($1))",
                        RUN_LOCK_NAME,
                    )
                except Exception:
                    pass
            await connection.close()

def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None

def _stats(net_values: list[float]) -> dict[str, Any]:
    values = [float(value) for value in net_values if math.isfinite(float(value))]
    gains = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    return {
        "n": len(values),
        "average_net_r": round(statistics.fmean(values), 6) if values else None,
        "median_net_r": round(statistics.median(values), 6) if values else None,
        "positive_rate_pct": round(sum(value > 0 for value in values) / len(values) * 100, 3) if values else None,
        "profit_factor": round(gains / losses, 6) if losses > 0 else None,
        "total_net_r": round(sum(values), 6),
    }

def _quantile(values: list[float], probability: float) -> float:
    rows = sorted(values)
    if not rows:
        raise ValueError("quantile requires values")
    if len(rows) == 1:
        return rows[0]
    position = (len(rows) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return rows[lower]
    weight = position - lower
    return rows[lower] * (1.0 - weight) + rows[upper] * weight

def _bin(value: float, cuts: tuple[float, float, float]) -> int:
    if value <= cuts[0]:
        return 1
    if value <= cuts[1]:
        return 2
    if value <= cuts[2]:
        return 3
    return 4

def profile_numeric_feature(rows: list[dict[str, Any]], feature: str) -> dict[str, Any]:
    discovery_pairs = [
        (_number(row.get(feature)), _number(row.get("base_net_r")))
        for row in rows
        if row.get("dataset_split") == "DEVELOPMENT"
    ]
    discovery_pairs = [(x, y) for x, y in discovery_pairs if x is not None and y is not None]
    if len(discovery_pairs) < 40:
        return {"feature": feature, "status": "INSUFFICIENT_DISCOVERY_SAMPLE", "n": len(discovery_pairs)}

    cuts = tuple(_quantile([x for x, _ in discovery_pairs], p) for p in (0.25, 0.50, 0.75))
    output: dict[str, Any] = {
        "feature": feature,
        "status": "OK",
        "cut_points_discovery_only": [round(value, 8) for value in cuts],
        "discovery_bins": [],
        "validation_bins": [],
    }
    for split, key in (("DEVELOPMENT", "discovery_bins"), ("VALIDATION", "validation_bins")):
        grouped = {index: [] for index in range(1, 5)}
        feature_values = {index: [] for index in range(1, 5)}
        for row in rows:
            if row.get("dataset_split") != split:
                continue
            x = _number(row.get(feature))
            y = _number(row.get("base_net_r"))
            if x is None or y is None:
                continue
            index = _bin(x, cuts)
            grouped[index].append(y)
            feature_values[index].append(x)
        output[key] = [
            {
                "quartile": index,
                "feature_min": round(min(feature_values[index]), 8) if feature_values[index] else None,
                "feature_max": round(max(feature_values[index]), 8) if feature_values[index] else None,
                **_stats(grouped[index]),
            }
            for index in range(1, 5)
        ]
    return output

def profile_categorical_feature(rows: list[dict[str, Any]], feature: str) -> dict[str, Any]:
    values = sorted({str(row.get(feature)) for row in rows if row.get(feature) is not None})
    result: dict[str, Any] = {"feature": feature, "groups": []}
    for value in values:
        item: dict[str, Any] = {"value": value}
        for split, key in (("DEVELOPMENT", "discovery"), ("VALIDATION", "validation")):
            nets = [
                _number(row.get("base_net_r"))
                for row in rows
                if row.get("dataset_split") == split and str(row.get(feature)) == value
            ]
            item[key] = _stats([value for value in nets if value is not None])
        result["groups"].append(item)
    return result

def build_profile_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluable = [row for row in rows if _number(row.get("base_net_r")) is not None]
    discovery = [row for row in evaluable if row.get("dataset_split") == "DEVELOPMENT"]
    validation = [row for row in evaluable if row.get("dataset_split") == "VALIDATION"]
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "dataset_version": DATASET_VERSION,
        "membership": "candidate_built AND pass_structure_5m",
        "outcome": "base_net_r = 8h path net R after 20 bps",
        "counts": {
            "materialized_opportunities": len(rows),
            "outcome_evaluable": len(evaluable),
            "discovery_evaluable": len(discovery),
            "validation_evaluable": len(validation),
        },
        "baseline": {
            "discovery": _stats([float(row["base_net_r"]) for row in discovery]),
            "validation": _stats([float(row["base_net_r"]) for row in validation]),
        },
        "numeric_profiles": {
            feature: profile_numeric_feature(evaluable, feature)
            for feature in PROFILE_NUMERIC_FEATURES
        },
        "categorical_profiles": {
            feature: profile_categorical_feature(evaluable, feature)
            for feature in PROFILE_CATEGORICAL_FEATURES
        },
        "interpretation_policy": {
            "promotion_allowed": False,
            "next_step": "Use profiles to preregister a small number of mechanistic hypotheses, then test untouched OOS/walk-forward.",
            "multiple_testing_warning": True,
        },
        "warnings": list(WARNINGS),
    }
