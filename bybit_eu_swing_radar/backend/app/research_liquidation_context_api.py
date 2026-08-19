"""Hidden research-only Coinalyze liquidation forward-context API."""
from __future__ import annotations

import asyncio
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import asyncpg
from research.research_snapshot_history import append_snapshot_history
import httpx
from fastapi import Depends, FastAPI, HTTPException

from research.liquidation_context_shadow import (
    INTERVAL,
    LOOKBACK_HOURS,
    MAX_MARKET_ATTEMPTS_PER_SYMBOL,
    MAX_SYMBOLS,
    SPEC_VERSION,
    build_snapshot,
    build_symbol_context,
    history_map,
    normalize_exchange_names,
    select_market_candidates,
    spec,
)

COINALYZE_BASE_URL = os.getenv("COINALYZE_BASE_URL", "https://api.coinalyze.net/v1").rstrip("/")
COINALYZE_API_KEY = os.getenv("COINALYZE_API_KEY", "").strip()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_liquidation_context_snapshots (
    spec_version TEXT NOT NULL,
    captured_hour TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    symbol_count INTEGER NOT NULL,
    coverage_count INTEGER NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (spec_version, captured_hour)
);
CREATE INDEX IF NOT EXISTS idx_research_liquidation_context_time
ON research_liquidation_context_snapshots(captured_at DESC);
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


def _regime_symbols(payload: Mapping[str, Any]) -> list[str]:
    raw = payload.get("symbols")
    result: list[str] = []
    if isinstance(raw, Mapping):
        result = [str(symbol).upper() for symbol in raw]
    elif isinstance(raw, list):
        result = [
            str(row.get("symbol") or "").upper()
            for row in raw
            if isinstance(row, Mapping)
        ]
    return [symbol for symbol in dict.fromkeys(result) if symbol.endswith("USDC")][:MAX_SYMBOLS]


async def _load_target_symbols() -> tuple[list[str], str | None]:
    connection = await asyncpg.connect(_database_url())
    try:
        row = await connection.fetchrow(
            """
            SELECT captured_at,payload
            FROM research_market_regime_snapshots
            WHERE spec_version='market-regime-shadow-v1'
            ORDER BY captured_at DESC
            LIMIT 1
            """
        )
    finally:
        await connection.close()
    if row is None:
        raise RuntimeError("market-regime-shadow-v1 snapshot is unavailable")
    payload = _decode(row["payload"])
    symbols = _regime_symbols(payload)
    if not symbols:
        raise RuntimeError("market-regime-shadow-v1 has no USDC symbol coverage")
    return symbols, row["captured_at"].isoformat()


async def _coinalyze_get(
    client: httpx.AsyncClient,
    path: str,
    params: Mapping[str, Any] | None = None,
) -> Any:
    if not COINALYZE_API_KEY:
        raise RuntimeError("COINALYZE_API_KEY is not configured")
    headers = {"api_key": COINALYZE_API_KEY}
    response = await client.get(f"{COINALYZE_BASE_URL}{path}", params=dict(params or {}), headers=headers)
    if response.status_code == 429:
        try:
            wait_seconds = math.ceil(float(response.headers.get("Retry-After", "1")))
        except (TypeError, ValueError):
            wait_seconds = 1
        await asyncio.sleep(max(1, min(wait_seconds, 65)))
        response = await client.get(f"{COINALYZE_BASE_URL}{path}", params=dict(params or {}), headers=headers)
    response.raise_for_status()
    return response.json()


async def _liquidation_batch(
    client: httpx.AsyncClient,
    market_symbols: list[str],
    from_ts: int,
    to_ts: int,
) -> dict[str, list[dict[str, Any]]]:
    if not market_symbols:
        return {}
    if len(market_symbols) > 20:
        raise RuntimeError("Coinalyze liquidation batch exceeds 20-symbol endpoint maximum")
    payload = await _coinalyze_get(
        client,
        "/liquidation-history",
        {
            "symbols": ",".join(market_symbols),
            "interval": INTERVAL,
            "from": from_ts,
            "to": to_ts,
            "convert_to_usd": "true",
        },
    )
    return history_map(payload)


async def build_current_snapshot() -> dict[str, Any]:
    symbols, regime_captured_at = await _load_target_symbols()
    bases = [symbol[:-4] for symbol in symbols]
    captured_at = datetime.now(timezone.utc)
    to_ts = int(captured_at.timestamp())
    from_ts = int((captured_at - timedelta(hours=LOOKBACK_HOURS)).timestamp())

    async with httpx.AsyncClient(timeout=30.0) as client:
        exchanges_payload = await _coinalyze_get(client, "/exchanges")
        markets_payload = await _coinalyze_get(client, "/future-markets")
        if not isinstance(exchanges_payload, list) or not isinstance(markets_payload, list):
            raise RuntimeError("Coinalyze market metadata response is not a list")
        exchange_names = normalize_exchange_names(exchanges_payload)
        if not exchange_names:
            raise RuntimeError("Coinalyze /exchanges returned no code mappings")
        candidates = select_market_candidates(markets_payload, exchange_names, bases)

        primary_by_symbol: dict[str, dict[str, Any]] = {}
        primary_market_symbols: list[str] = []
        for spot_symbol, base in zip(symbols, bases):
            options = candidates.get(base) or []
            if options:
                primary_by_symbol[spot_symbol] = options[0]
                primary_market_symbols.append(str(options[0]["symbol"]))

        primary_history = await _liquidation_batch(
            client, list(dict.fromkeys(primary_market_symbols)), from_ts, to_ts
        )

        fallback_by_symbol: dict[str, dict[str, Any]] = {}
        fallback_market_symbols: list[str] = []
        for spot_symbol, base in zip(symbols, bases):
            primary = primary_by_symbol.get(spot_symbol)
            primary_rows = primary_history.get(str((primary or {}).get("symbol") or ""), [])
            options = candidates.get(base) or []
            if primary_rows or len(options) < MAX_MARKET_ATTEMPTS_PER_SYMBOL:
                continue
            fallback = options[1]
            fallback_by_symbol[spot_symbol] = fallback
            fallback_market_symbols.append(str(fallback["symbol"]))

        fallback_history = await _liquidation_batch(
            client, list(dict.fromkeys(fallback_market_symbols)), from_ts, to_ts
        )

    rows: list[dict[str, Any]] = []
    for spot_symbol in symbols:
        primary = primary_by_symbol.get(spot_symbol)
        primary_symbol = str((primary or {}).get("symbol") or "")
        primary_rows = primary_history.get(primary_symbol, [])
        fallback = fallback_by_symbol.get(spot_symbol)
        fallback_symbol = str((fallback or {}).get("symbol") or "")
        fallback_rows = fallback_history.get(fallback_symbol, [])
        chosen = primary
        chosen_rows = primary_rows
        fallback_used = False
        if not primary_rows and fallback_rows:
            chosen = fallback
            chosen_rows = fallback_rows
            fallback_used = True
        attempted = [value for value in (primary_symbol, fallback_symbol) if value]
        rows.append(
            build_symbol_context(
                spot_symbol,
                chosen,
                chosen_rows,
                fallback_used=fallback_used,
                attempted_markets=attempted,
            )
        )

    symbol_calls = len(primary_market_symbols) + len(fallback_market_symbols)
    if symbol_calls > MAX_SYMBOLS * MAX_MARKET_ATTEMPTS_PER_SYMBOL:
        raise RuntimeError("liquidation symbol-call budget exceeded")
    snapshot = build_snapshot(
        rows,
        captured_at=captured_at,
        source_commit_sha=os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
        metadata={
            "provider": "Coinalyze",
            "regime_captured_at": regime_captured_at,
            "window_from": datetime.fromtimestamp(from_ts, tz=timezone.utc).isoformat(),
            "window_to": datetime.fromtimestamp(to_ts, tz=timezone.utc).isoformat(),
            "primary_market_count": len(primary_market_symbols),
            "fallback_market_count": len(fallback_market_symbols),
            "liquidation_symbol_calls": symbol_calls,
            "max_liquidation_symbol_calls": MAX_SYMBOLS * MAX_MARKET_ATTEMPTS_PER_SYMBOL,
            "metadata_http_requests": 2,
            "liquidation_http_requests": 1 + int(bool(fallback_market_symbols)),
        },
    )
    return snapshot


async def persist_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    captured_at = datetime.fromisoformat(str(snapshot["captured_at"]).replace("Z", "+00:00"))
    captured_hour = captured_at.replace(minute=0, second=0, microsecond=0)
    coverage = snapshot.get("coverage") or {}
    connection = await asyncpg.connect(_database_url())
    try:
        await connection.execute(SCHEMA_SQL)
        history = await append_snapshot_history(
            connection,
            research_family="liquidation-context",
            spec_version=SPEC_VERSION,
            captured_at=captured_at,
            capture_bucket=captured_hour,
            source_commit_sha=snapshot.get("source_commit_sha"),
            snapshot=snapshot,
        )
        snapshot["immutable_history"] = history
        await connection.execute(
            """
            INSERT INTO research_liquidation_context_snapshots (
                spec_version,captured_hour,captured_at,source_commit_sha,
                symbol_count,coverage_count,payload,updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,NOW())
            ON CONFLICT (spec_version,captured_hour) DO UPDATE SET
                captured_at=EXCLUDED.captured_at,
                source_commit_sha=EXCLUDED.source_commit_sha,
                symbol_count=EXCLUDED.symbol_count,
                coverage_count=EXCLUDED.coverage_count,
                payload=EXCLUDED.payload,
                updated_at=NOW()
            """,
            SPEC_VERSION,
            captured_hour,
            captured_at,
            snapshot.get("source_commit_sha"),
            int(snapshot.get("symbol_count") or 0),
            int(coverage.get("available") or 0),
            json.dumps(snapshot, separators=(",", ":"), default=str),
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
            FROM research_liquidation_context_snapshots
            WHERE spec_version=$1
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            SPEC_VERSION,
        )
        total = await connection.fetchval(
            "SELECT COUNT(*)::int FROM research_liquidation_context_snapshots WHERE spec_version=$1",
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
        "context_only": True,
        "label_free": True,
        "live_strategy_mutated": False,
        "execution_proof": False,
        "promotion_allowed": False,
        "spec": spec(),
        "snapshot_count": int(total or 0),
        "latest": payload,
    }


def attach_liquidation_context_research(
    app: FastAPI, require_api_key: Callable[..., Any]
) -> None:
    @app.get(
        "/v1/research/liquidation-context/spec",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def liquidation_context_spec() -> dict[str, Any]:
        return spec()

    @app.post(
        "/v1/research/liquidation-context/capture",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def liquidation_context_capture() -> dict[str, Any]:
        try:
            return await capture_current_snapshot()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research liquidation-context capture unavailable: {type(exc).__name__}",
            ) from exc

    @app.get(
        "/v1/research/liquidation-context/status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def liquidation_context_status() -> dict[str, Any]:
        try:
            return await status_payload()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research liquidation-context status unavailable: {type(exc).__name__}",
            ) from exc
