"""Durable runtime heartbeat for the standalone microstructure recorder.

This module is research-only.  It provides operational observability for the
separate recorder service without coupling the FastAPI process to the websocket
collector lifecycle.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

import asyncpg

RECORDER_ID = "microstructure-recorder-standalone-v1"
DEFAULT_MAX_HEARTBEAT_AGE_SECONDS = 30.0

CREATE_RUNTIME_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research_microstructure_recorder_runtime (
    recorder_id TEXT PRIMARY KEY,
    heartbeat_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    service_id TEXT,
    service_name TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

UPSERT_RUNTIME_SQL = """
INSERT INTO research_microstructure_recorder_runtime (
    recorder_id,heartbeat_at,source_commit_sha,service_id,service_name,payload,updated_at
) VALUES ($1,$2,$3,$4,$5,$6::jsonb,NOW())
ON CONFLICT (recorder_id) DO UPDATE SET
    heartbeat_at=EXCLUDED.heartbeat_at,
    source_commit_sha=EXCLUDED.source_commit_sha,
    service_id=EXCLUDED.service_id,
    service_name=EXCLUDED.service_name,
    payload=EXCLUDED.payload,
    updated_at=NOW()
"""


def _decode(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        loaded = json.loads(value)
        return dict(loaded) if isinstance(loaded, dict) else {}
    return {}


async def persist_runtime_status(
    database_url: str,
    payload: dict[str, Any],
    *,
    source_commit_sha: str | None,
    service_id: str | None,
    service_name: str | None,
    recorder_id: str = RECORDER_ID,
) -> None:
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    heartbeat_at = datetime.now(timezone.utc)
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(CREATE_RUNTIME_TABLE_SQL)
        await connection.execute(
            UPSERT_RUNTIME_SQL,
            recorder_id,
            heartbeat_at,
            source_commit_sha,
            service_id,
            service_name,
            json.dumps(payload, separators=(",", ":")),
        )
    finally:
        await connection.close()


def _missing_payload(expected_symbols: Iterable[str], reason: str) -> dict[str, Any]:
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "enabled": True,
        "running": False,
        "singleton_acquired": False,
        "connected": False,
        "process_role": "standalone",
        "external_service_healthy": False,
        "heartbeat_age_seconds": None,
        "source_commit_sha": None,
        "service_id": None,
        "service_name": None,
        "symbols": list(expected_symbols),
        "messages": 0,
        "rows_written": 0,
        "last_message_at": None,
        "last_write_at": None,
        "last_error_at": None,
        "last_error": reason,
        "status_reason": reason,
    }


async def load_runtime_status(
    database_url: str,
    expected_symbols: Iterable[str],
    *,
    recorder_id: str = RECORDER_ID,
    max_heartbeat_age_seconds: float = DEFAULT_MAX_HEARTBEAT_AGE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    wanted = tuple(str(symbol).upper() for symbol in expected_symbols)
    if not database_url:
        return _missing_payload(wanted, "DATABASE_URL is not configured")
    connection = await asyncpg.connect(database_url)
    try:
        try:
            row = await connection.fetchrow(
                """
                SELECT heartbeat_at,source_commit_sha,service_id,service_name,payload
                FROM research_microstructure_recorder_runtime
                WHERE recorder_id=$1
                LIMIT 1
                """,
                recorder_id,
            )
        except asyncpg.UndefinedTableError:
            row = None
    finally:
        await connection.close()
    if row is None:
        return _missing_payload(wanted, "standalone recorder heartbeat is unavailable")

    heartbeat_at = row["heartbeat_at"]
    reference = now or datetime.now(timezone.utc)
    if heartbeat_at.tzinfo is None:
        heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
    heartbeat_at = heartbeat_at.astimezone(timezone.utc)
    age = max(0.0, (reference.astimezone(timezone.utc) - heartbeat_at).total_seconds())
    payload = _decode(row["payload"])
    symbols = [str(item).upper() for item in payload.get("symbols") or []]
    expected_ok = symbols == list(wanted)
    healthy = (
        age <= max_heartbeat_age_seconds
        and payload.get("running") is True
        and payload.get("singleton_acquired") is True
        and payload.get("connected") is True
        and expected_ok
    )
    return {
        **payload,
        "research_only": True,
        "live_strategy_mutated": False,
        "process_role": "standalone",
        "external_service_healthy": healthy,
        "heartbeat_at": heartbeat_at.isoformat(),
        "heartbeat_age_seconds": age,
        "source_commit_sha": row["source_commit_sha"],
        "service_id": row["service_id"],
        "service_name": row["service_name"],
        "status_reason": "ok" if healthy else "standalone recorder heartbeat is stale or unhealthy",
    }
