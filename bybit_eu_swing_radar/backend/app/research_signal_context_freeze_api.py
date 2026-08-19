"""Hidden API for immutable, label-blind Signal-Time Context Freeze v1."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import asyncpg
from fastapi import Depends, FastAPI, HTTPException

from research.cross_layer_context import SPEC_VERSION as CROSS_LAYER_SPEC_VERSION
from research.microstructure.alignment import ALIGNMENT_SQL, build_feature_rows
from research.microstructure.collector import DEFAULT_SYMBOLS
from research.signal_context_freeze import (
    SPEC_VERSION,
    STRATEGY_VERSION,
    build_freeze_payload,
    sample_gate,
    spec,
)

MAX_CAPTURE_BATCH = 100
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_signal_context_freezes (
    spec_version TEXT NOT NULL,
    signal_key TEXT NOT NULL,
    signal_id BIGINT NOT NULL,
    strategy_version TEXT NOT NULL,
    signal_class TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    setup_type TEXT NOT NULL,
    opened_at TIMESTAMPTZ NOT NULL,
    frozen_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    cross_layer_status TEXT NOT NULL,
    cross_layer_captured_at TIMESTAMPTZ,
    microstructure_status TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (spec_version, signal_key)
);
CREATE INDEX IF NOT EXISTS idx_research_signal_context_freezes_opened
ON research_signal_context_freezes(opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_signal_context_freezes_symbol
ON research_signal_context_freezes(symbol, opened_at DESC);

DO $immutability$
BEGIN
    IF to_regprocedure(current_schema() || '.reject_research_signal_context_freezes_mutation()') IS NULL THEN
        EXECUTE $create_function$
            CREATE FUNCTION reject_research_signal_context_freezes_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            BEGIN
                RAISE EXCEPTION 'research_signal_context_freezes is append-only';
            END;
            $function$
        $create_function$;
    END IF;
END
$immutability$;

DO $immutability$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'research_signal_context_freezes'::regclass
          AND tgname = 'trg_research_signal_context_freezes_no_row_mutation'
          AND NOT tgisinternal
    ) THEN
        EXECUTE 'CREATE TRIGGER trg_research_signal_context_freezes_no_row_mutation '
                'BEFORE UPDATE OR DELETE ON research_signal_context_freezes '
                'FOR EACH ROW EXECUTE FUNCTION reject_research_signal_context_freezes_mutation()';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'research_signal_context_freezes'::regclass
          AND tgname = 'trg_research_signal_context_freezes_no_truncate'
          AND NOT tgisinternal
    ) THEN
        EXECUTE 'CREATE TRIGGER trg_research_signal_context_freezes_no_truncate '
                'BEFORE TRUNCATE ON research_signal_context_freezes '
                'FOR EACH STATEMENT EXECUTE FUNCTION reject_research_signal_context_freezes_mutation()';
    END IF;
END
$immutability$;
"""

# Deliberately excludes journal status, outcome, closed_at, exit_reason, gross_r,
# net_r, MFE/MAE, targets hit, and all post-signal result fields.
JOURNAL_SIGNAL_SQL = """
SELECT
    j.id,j.signal_key,j.strategy_version,j.signal_class,j.symbol,j.side,
    j.opened_at,j.setup_type
FROM day_trade_signal_journal AS j
LEFT JOIN research_signal_context_freezes AS f
  ON f.spec_version=$1 AND f.signal_key=j.signal_key
WHERE j.strategy_version=$2
  AND j.opened_at <= $3
  AND f.signal_key IS NULL
ORDER BY j.opened_at,j.id
LIMIT $4
"""


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is not configured")
    return value


def _recorder_symbols() -> tuple[str, ...]:
    raw = os.getenv("MICROSTRUCTURE_SYMBOLS", ",".join(DEFAULT_SYMBOLS))
    symbols = tuple(
        dict.fromkeys(
            item.strip().upper()
            for item in raw.split(",")
            if item.strip().upper().endswith("USDC")
        )
    )
    return symbols[:12]


def _decode(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    return {}


async def _load_cross_layer(
    connection: asyncpg.Connection, opened_at: datetime
) -> dict[str, Any] | None:
    try:
        row = await connection.fetchrow(
            """
            SELECT captured_at,source_commit_sha,payload
            FROM research_cross_layer_context_snapshots
            WHERE spec_version=$1 AND captured_at <= $2
            ORDER BY captured_at DESC
            LIMIT 1
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
        "payload": _decode(row["payload"]),
    }


async def _load_microstructure_features(
    connection: asyncpg.Connection,
    signals: list[Mapping[str, Any]],
    recorder_symbols: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    tracked = [
        row for row in signals if str(row.get("symbol") or "").upper() in recorder_symbols
    ]
    if not tracked or not recorder_symbols:
        return {}
    since = min(row["opened_at"] for row in tracked)
    until = max(row["opened_at"] for row in tracked) + timedelta(microseconds=1)
    try:
        raw = await connection.fetch(ALIGNMENT_SQL, list(recorder_symbols), since, until)
    except asyncpg.UndefinedTableError:
        return {}
    features = build_feature_rows(raw, bucket_seconds=5)
    wanted_keys = {str(row.get("signal_key") or "") for row in tracked}
    return {
        str(item.get("signal_key") or ""): item
        for item in features
        if str(item.get("signal_key") or "") in wanted_keys
    }


async def capture_current_batch(limit: int = MAX_CAPTURE_BATCH) -> dict[str, Any]:
    batch_limit = max(1, min(int(limit), MAX_CAPTURE_BATCH))
    frozen_at = datetime.now(timezone.utc)
    source_sha = os.getenv("RAILWAY_GIT_COMMIT_SHA") or None
    recorder_symbols = _recorder_symbols()
    connection = await asyncpg.connect(_database_url())
    try:
        await connection.execute(SCHEMA_SQL)
        raw_signals = await connection.fetch(
            JOURNAL_SIGNAL_SQL,
            SPEC_VERSION,
            STRATEGY_VERSION,
            frozen_at,
            batch_limit,
        )
        signals = [dict(row) for row in raw_signals]
        micro_by_key = await _load_microstructure_features(
            connection, signals, recorder_symbols
        )
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
                    spec_version,signal_key,signal_id,strategy_version,
                    signal_class,symbol,side,setup_type,opened_at,frozen_at,
                    source_commit_sha,cross_layer_status,cross_layer_captured_at,
                    microstructure_status,payload
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb
                )
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
        "source_commit_sha": source_sha,
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
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
        counts_raw = await connection.fetchrow(
            """
            SELECT
                COUNT(*)::int AS total,
                COUNT(*) FILTER (WHERE side='long')::int AS long_count,
                COUNT(*) FILTER (WHERE side='short')::int AS short_count,
                COUNT(DISTINCT ((opened_at AT TIME ZONE 'UTC')::date))::int AS distinct_utc_days,
                COUNT(*) FILTER (WHERE cross_layer_status='FRESH')::int AS cross_layer_covered,
                COUNT(*) FILTER (WHERE microstructure_status='ALIGNED')::int AS microstructure_aligned,
                COUNT(*) FILTER (WHERE microstructure_status='NOT_TRACKED')::int AS microstructure_not_tracked,
                COUNT(*) FILTER (WHERE microstructure_status='NO_PRE_SIGNAL_BUCKETS')::int AS microstructure_no_buckets,
                MIN(opened_at) AS first_opened_at,
                MAX(opened_at) AS last_opened_at
            FROM research_signal_context_freezes
            WHERE spec_version=$1 AND strategy_version=$2
            """,
            SPEC_VERSION,
            STRATEGY_VERSION,
        )
        journal_count = await connection.fetchval(
            "SELECT COUNT(*)::int FROM day_trade_signal_journal WHERE strategy_version=$1",
            STRATEGY_VERSION,
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
    journal_total = int(journal_count or 0)
    return {
        "spec_version": SPEC_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit_sha": os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "journal_signal_count": journal_total,
        "frozen_signal_count": frozen_count,
        "freeze_coverage_pct": (
            round(frozen_count / journal_total * 100.0, 3) if journal_total else 0.0
        ),
        "counts": counts,
        "future_effect_gate": gate,
        "by_symbol": [dict(row) for row in by_symbol_rows],
        "recorder_symbols": list(_recorder_symbols()),
    }


def attach_signal_context_freeze_research(
    app: FastAPI, require_api_key: Callable[..., Any]
) -> None:
    @app.get(
        "/v1/research/signal-context-freeze/spec",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def signal_context_freeze_spec() -> dict[str, Any]:
        return spec()

    @app.post(
        "/v1/research/signal-context-freeze/capture",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def signal_context_freeze_capture() -> dict[str, Any]:
        try:
            return await capture_current_batch()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research signal-context freeze unavailable: {type(exc).__name__}",
            ) from exc

    @app.get(
        "/v1/research/signal-context-freeze/status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def signal_context_freeze_status() -> dict[str, Any]:
        try:
            return await status_payload()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research signal-context freeze status unavailable: {type(exc).__name__}",
            ) from exc
