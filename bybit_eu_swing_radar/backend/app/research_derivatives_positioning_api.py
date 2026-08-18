"""Research-only forward derivatives-positioning capture/status API."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import asyncpg
from fastapi import Depends, FastAPI, HTTPException

from research.derivatives_positioning_shadow import (
    SPEC_VERSION,
    build_snapshot,
    classify_symbol,
    spec,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_derivatives_positioning_snapshots (
    spec_version TEXT NOT NULL,
    captured_hour TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    symbol_count INTEGER NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (spec_version, captured_hour)
);
CREATE INDEX IF NOT EXISTS idx_research_derivatives_positioning_time
ON research_derivatives_positioning_snapshots(captured_at DESC);
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


def _regime_symbol_map(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalize market-regime v1 symbols from its canonical list payload.

    Dict input is accepted too so stored/derived representations remain
    schema-tolerant, but canonical market-regime-shadow-v1 emits a list.
    """
    raw = snapshot.get("symbols")
    result: dict[str, dict[str, Any]] = {}
    if isinstance(raw, Mapping):
        for symbol, item in raw.items():
            if isinstance(item, Mapping):
                normalized = str(symbol).upper()
                if normalized:
                    row = dict(item)
                    row.setdefault("symbol", normalized)
                    result[normalized] = row
        return result
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            symbol = str(item.get("symbol") or "").upper()
            if symbol:
                result[symbol] = dict(item)
    return result


def _extract_liquidation_context(value: Any, wanted: set[str]) -> dict[str, dict[str, Any]]:
    """Collect cached symbol-level Coinalyze contexts without assuming scan schema."""
    result: dict[str, dict[str, Any]] = {}

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            symbol = str(node.get("symbol") or "").upper()
            derivatives = node.get("derivatives")
            if symbol in wanted and isinstance(derivatives, Mapping):
                if (
                    derivatives.get("long_liquidations_24h_usd") is not None
                    or derivatives.get("short_liquidations_24h_usd") is not None
                ):
                    result.setdefault(symbol, dict(derivatives))
            for nested in node.values():
                visit(nested)
        elif isinstance(node, list):
            for nested in node:
                visit(nested)

    visit(value)
    return result


async def _load_inputs(connection: asyncpg.Connection) -> tuple[list[str], dict[str, Any], dict[str, Any], dict[str, Any]]:
    regime_row = await connection.fetchrow(
        """
        SELECT payload
        FROM research_market_regime_snapshots
        WHERE spec_version='market-regime-shadow-v1'
        ORDER BY captured_at DESC
        LIMIT 1
        """
    )
    if regime_row is None:
        raise RuntimeError("market-regime-shadow-v1 snapshot is unavailable")
    regime_snapshot = _decode(regime_row["payload"])
    regime_symbols = _regime_symbol_map(regime_snapshot)
    if not regime_symbols:
        raise RuntimeError("market-regime-shadow-v1 symbol coverage is empty")
    symbols = list(regime_symbols.keys())[:8]

    keys = [f"day_trade_flow:{symbol}" for symbol in symbols]
    flow_rows = await connection.fetch(
        "SELECT cache_key,payload FROM radar_cache WHERE cache_key = ANY($1::text[])",
        keys,
    )
    flow_map: dict[str, Any] = {}
    for row in flow_rows:
        symbol = str(row["cache_key"]).split(":", 1)[-1].upper()
        flow_map[symbol] = _decode(row["payload"])

    liquidation_map: dict[str, Any] = {}
    try:
        liquidation_row = await connection.fetchrow(
            """
            SELECT captured_at,payload
            FROM research_liquidation_context_snapshots
            WHERE spec_version='liquidation-context-shadow-v1'
              AND captured_at <= NOW()
              AND captured_at >= NOW() - INTERVAL '2 hours'
            ORDER BY captured_at DESC
            LIMIT 1
            """
        )
    except asyncpg.UndefinedTableError:
        liquidation_row = None

    if liquidation_row is not None:
        liquidation_snapshot = _decode(liquidation_row["payload"])
        raw_liquidations = liquidation_snapshot.get("symbols")
        wanted = set(symbols)
        if isinstance(raw_liquidations, Mapping):
            for symbol, item in raw_liquidations.items():
                normalized = str(symbol).upper()
                if (
                    normalized in wanted
                    and isinstance(item, Mapping)
                    and item.get("coverage") is True
                ):
                    row = dict(item)
                    row.setdefault("symbol", normalized)
                    liquidation_map[normalized] = row
        elif isinstance(raw_liquidations, list):
            for item in raw_liquidations:
                if not isinstance(item, Mapping):
                    continue
                normalized = str(item.get("symbol") or "").upper()
                if normalized in wanted and item.get("coverage") is True:
                    liquidation_map[normalized] = dict(item)

    return symbols, regime_snapshot, flow_map, liquidation_map


async def build_current_snapshot() -> dict[str, Any]:
    connection = await asyncpg.connect(_database_url())
    try:
        symbols, regime_snapshot, flow_map, liquidation_map = await _load_inputs(connection)
    finally:
        await connection.close()

    regime_symbols = _regime_symbol_map(regime_snapshot)
    rows = [
        classify_symbol(
            symbol,
            flow_map.get(symbol),
            regime_symbols.get(symbol),
            liquidation_map.get(symbol),
        )
        for symbol in symbols
    ]
    snapshot = build_snapshot(
        rows,
        captured_at=datetime.now(timezone.utc),
        source_commit_sha=os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
    )
    regime_spec = regime_snapshot.get("spec") or {}
    snapshot["upstream"] = {
        "market_regime_spec_version": (
            regime_spec.get("version") if isinstance(regime_spec, Mapping) else None
        ) or "market-regime-shadow-v1",
        "market_regime_captured_at": regime_snapshot.get("captured_at"),
        "flow_cache_symbols": sorted(flow_map.keys()),
        "liquidation_cache_symbols": sorted(liquidation_map.keys()),
        "liquidation_source_spec_version": "liquidation-context-shadow-v1",
    }
    return snapshot


async def persist_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    captured_at = datetime.fromisoformat(str(snapshot["captured_at"]).replace("Z", "+00:00"))
    captured_hour = captured_at.replace(minute=0, second=0, microsecond=0)
    connection = await asyncpg.connect(_database_url())
    try:
        await connection.execute(SCHEMA_SQL)
        await connection.execute(
            """
            INSERT INTO research_derivatives_positioning_snapshots (
                spec_version,captured_hour,captured_at,source_commit_sha,symbol_count,payload,updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6::jsonb,NOW())
            ON CONFLICT (spec_version,captured_hour) DO UPDATE SET
                captured_at=EXCLUDED.captured_at,
                source_commit_sha=EXCLUDED.source_commit_sha,
                symbol_count=EXCLUDED.symbol_count,
                payload=EXCLUDED.payload,
                updated_at=NOW()
            """,
            SPEC_VERSION,
            captured_hour,
            captured_at,
            snapshot.get("source_commit_sha"),
            int(snapshot.get("symbol_count") or 0),
            json.dumps(snapshot, separators=(",", ":")),
        )
    finally:
        await connection.close()
    return {**snapshot, "persisted": True, "captured_hour": captured_hour.isoformat()}


async def capture_current_snapshot() -> dict[str, Any]:
    return await persist_snapshot(await build_current_snapshot())


async def status_payload() -> dict[str, Any]:
    connection = await asyncpg.connect(_database_url())
    try:
        await connection.execute(SCHEMA_SQL)
        latest = await connection.fetchrow(
            """
            SELECT captured_at,captured_hour,source_commit_sha,payload
            FROM research_derivatives_positioning_snapshots
            WHERE spec_version=$1
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            SPEC_VERSION,
        )
        total = await connection.fetchval(
            "SELECT COUNT(*)::int FROM research_derivatives_positioning_snapshots WHERE spec_version=$1",
            SPEC_VERSION,
        )
    finally:
        await connection.close()

    latest_payload = None
    if latest:
        latest_payload = _decode(latest["payload"])
        latest_payload["captured_hour"] = latest["captured_hour"].isoformat()
        latest_payload["source_commit_sha"] = latest["source_commit_sha"]
    return {
        "research_only": True,
        "label_free": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "spec": spec(),
        "snapshot_count": int(total or 0),
        "latest": latest_payload,
    }


def attach_derivatives_positioning_research(app: FastAPI, require_api_key: Callable[..., Any]) -> None:
    @app.get(
        "/v1/research/derivatives-positioning/spec",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def derivatives_positioning_spec() -> dict[str, Any]:
        return spec()

    @app.post(
        "/v1/research/derivatives-positioning/capture",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def derivatives_positioning_capture() -> dict[str, Any]:
        try:
            return await capture_current_snapshot()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research derivatives-positioning capture unavailable: {type(exc).__name__}",
            ) from exc

    @app.get(
        "/v1/research/derivatives-positioning/status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def derivatives_positioning_status() -> dict[str, Any]:
        try:
            return await status_payload()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research derivatives-positioning status unavailable: {type(exc).__name__}",
            ) from exc

    # Research-only liquidation context collector. It is attached under the
    # existing research router and never enters live strategy/scoring/execution.
    from app.research_liquidation_context_api import attach_liquidation_context_research

    attach_liquidation_context_research(app, require_api_key)
