"""Hidden API for prospective-only Signal-Time Context Freeze v3."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable

import asyncpg
from fastapi import Depends, FastAPI, HTTPException

from app.research_signal_context_freeze_api import (
    SCHEMA_SQL,
    _load_microstructure_features,
    _recorder_symbols,
)
from research.signal_context_freeze import sample_gate
from research.signal_context_freeze_v3 import (
    CROSS_LAYER_SPEC_VERSION,
    SPEC_VERSION,
    STRATEGY_VERSION,
    build_freeze_payload,
    spec,
)

MAX_CAPTURE_BATCH = 100
JOURNAL_SIGNAL_SQL = """
SELECT
    j.id,j.signal_key,j.strategy_version,j.signal_class,j.symbol,j.side,
    j.opened_at,j.setup_type
FROM day_trade_signal_journal AS j
LEFT JOIN research_signal_context_freezes AS f
  ON f.spec_version=$1 AND f.signal_key=j.signal_key
WHERE j.strategy_version=$2
  AND j.opened_at >= $3
  AND j.opened_at <= $4
  AND f.signal_key IS NULL
ORDER BY j.opened_at,j.id
LIMIT $5
"""


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is not configured")
    return value


def _decode(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    return {}


async def _prospective_start_at(connection: asyncpg.Connection) -> datetime | None:
    try:
        return await connection.fetchval(
            """
            SELECT MIN(captured_at)
            FROM research_snapshot_history
            WHERE research_family='cross-layer-context-v2'
              AND spec_version=$1
            """,
            CROSS_LAYER_SPEC_VERSION,
        )
    except asyncpg.UndefinedTableError:
        return None


async def _load_cross_layer(connection: asyncpg.Connection, opened_at: datetime) -> dict[str, Any] | None:
    try:
        row = await connection.fetchrow(
            """
            SELECT captured_at,source_commit_sha,payload,payload_fingerprint
            FROM research_snapshot_history
            WHERE research_family='cross-layer-context-v2'
              AND spec_version=$1
              AND captured_at <= $2
            ORDER BY captured_at DESC LIMIT 1
            """,
            CROSS_LAYER_SPEC_VERSION,
            opened_at,
        )
    except asyncpg.UndefinedTableError:
        return None
    if row is None:
        return None
    return {
        "captured_at": row["captured_at"].isoformat(),
        "source_commit_sha": row["source_commit_sha"],
        "payload_fingerprint": row["payload_fingerprint"],
        "payload": _decode(row["payload"]),
    }


async def capture_current_batch(limit: int = MAX_CAPTURE_BATCH) -> dict[str, Any]:
    batch_limit = max(1, min(int(limit), MAX_CAPTURE_BATCH))
    frozen_at = datetime.now(timezone.utc)
    source_sha = os.getenv("RAILWAY_GIT_COMMIT_SHA") or None
    recorder_symbols = _recorder_symbols()
    connection = await asyncpg.connect(_database_url())
    try:
        await connection.execute(SCHEMA_SQL)
        start_at = await _prospective_start_at(connection)
        if start_at is None:
            signals = []
        else:
            raw_signals = await connection.fetch(
                JOURNAL_SIGNAL_SQL,
                SPEC_VERSION,
                STRATEGY_VERSION,
                start_at,
                frozen_at,
                batch_limit,
            )
            signals = [dict(row) for row in raw_signals]
        micro_by_key = await _load_microstructure_features(connection, signals, recorder_symbols)
        inserted = 0
        cross_counts: dict[str, int] = {}
        micro_counts: dict[str, int] = {}
        for signal in signals:
            cross_record = await _load_cross_layer(connection, signal["opened_at"])
            payload = build_freeze_payload(
                signal,
                cross_layer_record=cross_record,
                microstructure_feature=micro_by_key.get(str(signal["signal_key"])),
                recorder_symbols=recorder_symbols,
                frozen_at=frozen_at,
                source_commit_sha=source_sha,
            )
            cross = payload["cross_layer_context"]
            micro = payload["microstructure"]
            cross_status = str(cross["status"])
            micro_status = str(micro["status"])
            cross_counts[cross_status] = cross_counts.get(cross_status, 0) + 1
            micro_counts[micro_status] = micro_counts.get(micro_status, 0) + 1
            cross_captured = cross.get("captured_at")
            row = await connection.fetchrow(
                """
                INSERT INTO research_signal_context_freezes (
                    spec_version,signal_key,signal_id,strategy_version,signal_class,
                    symbol,side,setup_type,opened_at,frozen_at,source_commit_sha,
                    cross_layer_status,cross_layer_captured_at,microstructure_status,payload
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb)
                ON CONFLICT (spec_version,signal_key) DO NOTHING
                RETURNING signal_key
                """,
                SPEC_VERSION,
                payload["signal_key"],
                payload["signal_id"],
                payload["strategy_version"],
                payload["signal_class"],
                payload["symbol"],
                payload["side"],
                payload["setup_type"],
                signal["opened_at"],
                frozen_at,
                source_sha,
                cross_status,
                datetime.fromisoformat(cross_captured) if cross_captured else None,
                micro_status,
                json.dumps(payload, separators=(",", ":"), default=str),
            )
            inserted += 1 if row else 0
    finally:
        await connection.close()
    return {
        "spec_version": SPEC_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "captured_at": frozen_at.isoformat(),
        "prospective_start_at": start_at.isoformat() if start_at else None,
        "prospective_start_source": "immutable_raw_history_v1",
        "cross_layer_lookup_source": "research_snapshot_history",
        "source_commit_sha": source_sha,
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "historical_backfill_allowed": False,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "signals_examined": len(signals),
        "inserted": inserted,
        "recorder_symbols": list(recorder_symbols),
        "cross_layer_status_counts": cross_counts,
        "microstructure_status_counts": micro_counts,
    }


async def status_payload() -> dict[str, Any]:
    connection = await asyncpg.connect(_database_url())
    try:
        await connection.execute(SCHEMA_SQL)
        start_at = await _prospective_start_at(connection)
        counts_raw = await connection.fetchrow(
            """
            SELECT COUNT(*)::int AS total,
                   COUNT(*) FILTER (WHERE side='long')::int AS long_count,
                   COUNT(*) FILTER (WHERE side='short')::int AS short_count,
                   COUNT(DISTINCT ((opened_at AT TIME ZONE 'UTC')::date))::int AS distinct_utc_days,
                   COUNT(*) FILTER (WHERE cross_layer_status='FRESH')::int AS cross_layer_covered,
                   COUNT(*) FILTER (WHERE microstructure_status='ALIGNED')::int AS microstructure_aligned,
                   COUNT(*) FILTER (WHERE microstructure_status='NOT_TRACKED')::int AS microstructure_not_tracked,
                   COUNT(*) FILTER (WHERE microstructure_status='NO_PRE_SIGNAL_BUCKETS')::int AS microstructure_no_buckets,
                   MIN(opened_at) AS first_opened_at,MAX(opened_at) AS last_opened_at
            FROM research_signal_context_freezes
            WHERE spec_version=$1 AND strategy_version=$2
            """,
            SPEC_VERSION,
            STRATEGY_VERSION,
        )
        if start_at is None:
            journal_total = 0
            pre_v3_count = await connection.fetchval(
                "SELECT COUNT(*)::int FROM day_trade_signal_journal WHERE strategy_version=$1",
                STRATEGY_VERSION,
            )
        else:
            journal_total = await connection.fetchval(
                "SELECT COUNT(*)::int FROM day_trade_signal_journal WHERE strategy_version=$1 AND opened_at >= $2",
                STRATEGY_VERSION,
                start_at,
            )
            pre_v3_count = await connection.fetchval(
                "SELECT COUNT(*)::int FROM day_trade_signal_journal WHERE strategy_version=$1 AND opened_at < $2",
                STRATEGY_VERSION,
                start_at,
            )
        by_symbol_rows = await connection.fetch(
            """
            SELECT symbol,COUNT(*)::int AS count,
                   COUNT(*) FILTER (WHERE cross_layer_status='FRESH')::int AS cross_layer_fresh,
                   COUNT(*) FILTER (WHERE microstructure_status='ALIGNED')::int AS microstructure_aligned
            FROM research_signal_context_freezes
            WHERE spec_version=$1 AND strategy_version=$2
            GROUP BY symbol ORDER BY count DESC,symbol
            """,
            SPEC_VERSION,
            STRATEGY_VERSION,
        )
    finally:
        await connection.close()
    counts = dict(counts_raw or {})
    for key in ("first_opened_at", "last_opened_at"):
        value = counts.get(key)
        counts[key] = value.isoformat() if value is not None else None
    gate = sample_gate(counts)
    frozen_count = int(counts.get("total") or 0)
    prospective_journal_total = int(journal_total or 0)
    return {
        "spec_version": SPEC_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prospective_start_at": start_at.isoformat() if start_at else None,
        "prospective_start_source": "immutable_raw_history_v1",
        "cross_layer_lookup_source": "research_snapshot_history",
        "source_commit_sha": os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "historical_backfill_allowed": False,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "pre_v3_journal_signals_excluded": int(pre_v3_count or 0),
        "prospective_journal_signal_count": prospective_journal_total,
        "frozen_signal_count": frozen_count,
        "freeze_coverage_pct": round(frozen_count / prospective_journal_total * 100.0, 3) if prospective_journal_total else 0.0,
        "counts": counts,
        "future_effect_gate": gate,
        "by_symbol": [dict(row) for row in by_symbol_rows],
        "recorder_symbols": list(_recorder_symbols()),
        "spec": spec(),
    }


def attach_signal_context_freeze_v3_research(app: FastAPI, require_api_key: Callable[..., Any]) -> None:
    @app.get("/v1/research/signal-context-freeze-v3/spec", dependencies=[Depends(require_api_key)], include_in_schema=False)
    async def signal_context_freeze_v2_spec() -> dict[str, Any]:
        return spec()

    @app.post("/v1/research/signal-context-freeze-v3/capture", dependencies=[Depends(require_api_key)], include_in_schema=False)
    async def signal_context_freeze_v2_capture() -> dict[str, Any]:
        try:
            return await capture_current_batch()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Research signal-context freeze v2 unavailable: {type(exc).__name__}") from exc

    @app.get("/v1/research/signal-context-freeze-v3/status", dependencies=[Depends(require_api_key)], include_in_schema=False)
    async def signal_context_freeze_v2_status() -> dict[str, Any]:
        try:
            return await status_payload()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Research signal-context freeze v2 status unavailable: {type(exc).__name__}") from exc
