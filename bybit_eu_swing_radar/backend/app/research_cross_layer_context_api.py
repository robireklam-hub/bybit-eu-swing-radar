"""Hidden API for label-free Cross-Layer Context v1 snapshots."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable

import asyncpg
from fastapi import Depends, FastAPI, HTTPException

from research.cross_layer_context import SPEC_VERSION, build_context_snapshot, spec

SOURCE_LAYERS = {
    "market_regime": (
        "research_market_regime_snapshots",
        "market-regime-shadow-v1",
    ),
    "derivatives_positioning": (
        "research_derivatives_positioning_snapshots",
        "derivatives-positioning-shadow-v1",
    ),
    "relative_strength": (
        "research_relative_strength_snapshots",
        "relative-strength-shadow-v1",
    ),
    "event_tokenomics": (
        "research_event_tokenomics_snapshots",
        "event-tokenomics-shadow-v1",
    ),
    "btc_macro_cycle_etf": (
        "research_btc_macro_cycle_etf_snapshots",
        "btc-macro-cycle-etf-shadow-v1",
    ),
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_cross_layer_context_snapshots (
    spec_version TEXT NOT NULL,
    captured_hour TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    data_quality TEXT NOT NULL,
    symbol_count INTEGER NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (spec_version, captured_hour)
);
CREATE INDEX IF NOT EXISTS idx_research_cross_layer_context_time
ON research_cross_layer_context_snapshots(captured_at DESC);
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


async def _load_layer(
    connection: asyncpg.Connection,
    table: str,
    layer_spec_version: str,
    captured_at: datetime,
) -> dict[str, Any] | None:
    # Table names are selected only from the hard-coded SOURCE_LAYERS map.
    row = await connection.fetchrow(
        f"""
        SELECT captured_at,source_commit_sha,payload
        FROM {table}
        WHERE spec_version=$1 AND captured_at <= $2
        ORDER BY captured_at DESC
        LIMIT 1
        """,
        layer_spec_version,
        captured_at,
    )
    if row is None:
        return None
    return {
        "captured_at": row["captured_at"].isoformat(),
        "source_commit_sha": row["source_commit_sha"],
        "payload": _decode(row["payload"]),
    }


async def load_source_records(
    connection: asyncpg.Connection,
    captured_at: datetime,
) -> dict[str, dict[str, Any] | None]:
    result: dict[str, dict[str, Any] | None] = {}
    for name, (table, layer_version) in SOURCE_LAYERS.items():
        try:
            result[name] = await _load_layer(
                connection, table, layer_version, captured_at
            )
        except asyncpg.UndefinedTableError:
            result[name] = None
    return result


async def capture_current_snapshot() -> dict[str, Any]:
    captured_at = datetime.now(timezone.utc)
    source_sha = os.getenv("RAILWAY_GIT_COMMIT_SHA") or None
    connection = await asyncpg.connect(_database_url())
    try:
        await connection.execute(SCHEMA_SQL)
        records = await load_source_records(connection, captured_at)
        snapshot = build_context_snapshot(
            records,
            captured_at=captured_at,
            source_commit_sha=source_sha,
        )
        captured_hour = captured_at.replace(minute=0, second=0, microsecond=0)
        await connection.execute(
            """
            INSERT INTO research_cross_layer_context_snapshots (
                spec_version,captured_hour,captured_at,source_commit_sha,
                data_quality,symbol_count,payload,updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,NOW())
            ON CONFLICT (spec_version,captured_hour) DO UPDATE SET
                captured_at=EXCLUDED.captured_at,
                source_commit_sha=EXCLUDED.source_commit_sha,
                data_quality=EXCLUDED.data_quality,
                symbol_count=EXCLUDED.symbol_count,
                payload=EXCLUDED.payload,
                updated_at=NOW()
            """,
            SPEC_VERSION,
            captured_hour,
            captured_at,
            source_sha,
            snapshot["data_quality"],
            int(snapshot["symbol_count"]),
            json.dumps(snapshot, separators=(",", ":"), default=str),
        )
    finally:
        await connection.close()
    return {
        **snapshot,
        "persisted": True,
        "captured_hour": captured_hour.isoformat(),
    }


async def status_payload() -> dict[str, Any]:
    connection = await asyncpg.connect(_database_url())
    try:
        await connection.execute(SCHEMA_SQL)
        latest = await connection.fetchrow(
            """
            SELECT captured_at,captured_hour,source_commit_sha,data_quality,
                   symbol_count,payload
            FROM research_cross_layer_context_snapshots
            WHERE spec_version=$1
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            SPEC_VERSION,
        )
        count = await connection.fetchval(
            "SELECT COUNT(*)::int FROM research_cross_layer_context_snapshots WHERE spec_version=$1",
            SPEC_VERSION,
        )
    finally:
        await connection.close()
    payload = None
    if latest:
        payload = _decode(latest["payload"])
        payload["captured_hour"] = latest["captured_hour"].isoformat()
        payload["source_commit_sha"] = latest["source_commit_sha"]
    return {
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "spec": spec(),
        "snapshot_count": int(count or 0),
        "latest": payload,
    }


def attach_cross_layer_context_research(
    app: FastAPI, require_api_key: Callable[..., Any]
) -> None:
    @app.get(
        "/v1/research/cross-layer-context/spec",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def cross_layer_context_spec() -> dict[str, Any]:
        return spec()

    @app.post(
        "/v1/research/cross-layer-context/capture",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def cross_layer_context_capture() -> dict[str, Any]:
        try:
            return await capture_current_snapshot()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research cross-layer capture unavailable: {type(exc).__name__}",
            ) from exc

    @app.get(
        "/v1/research/cross-layer-context/status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def cross_layer_context_status() -> dict[str, Any]:
        try:
            return await status_payload()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research cross-layer status unavailable: {type(exc).__name__}",
            ) from exc
