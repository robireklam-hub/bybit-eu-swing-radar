"""Research-only forward relative-strength shadow capture and status API."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable

import asyncpg
from research.research_snapshot_history import append_snapshot_history
import httpx
from fastapi import Depends, FastAPI, HTTPException

from research.relative_strength_shadow import (
    SPEC_VERSION,
    build_snapshot,
    compute_symbol_metrics,
    parse_closed_daily_klines,
    spec,
)

TRACKED_UNIVERSE_SIZE = 20
MIN_ANALYZED_SYMBOLS = 12
DEFAULT_BYBIT_BASE_URL = "https://api.bybit.eu"
STABLE_BASES = {
    "USDC", "USDT", "DAI", "FDUSD", "TUSD", "USDE", "USDS", "PYUSD",
    "EUR", "EURC", "BUSD", "USD1", "RLUSD", "USDD", "USDQ",
}
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_relative_strength_snapshots (
    spec_version TEXT NOT NULL,
    captured_day DATE NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    universe_size INTEGER NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (spec_version, captured_day)
);
CREATE INDEX IF NOT EXISTS idx_research_relative_strength_time
ON research_relative_strength_snapshots(captured_at DESC);
"""


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is not configured")
    return value


def _bybit_base_url() -> str:
    value = os.getenv("BYBIT_BASE_URL", DEFAULT_BYBIT_BASE_URL).strip()
    return (value or DEFAULT_BYBIT_BASE_URL).rstrip("/")


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


async def _bybit_get(
    client: httpx.AsyncClient, path: str, params: dict[str, Any]
) -> dict[str, Any]:
    response = await client.get(
        f"{_bybit_base_url()}{path}", params=params, timeout=15.0
    )
    response.raise_for_status()
    payload = response.json()
    if int(payload.get("retCode", -1)) != 0:
        raise RuntimeError(f"Bybit public API error: {payload.get('retMsg')}")
    return payload


def _select_universe(
    tickers: list[dict[str, Any]], limit: int = TRACKED_UNIVERSE_SIZE
) -> list[str]:
    candidates: list[tuple[float, str]] = []
    for item in tickers:
        symbol = str(item.get("symbol") or "").upper()
        if not symbol.endswith("USDC") or len(symbol) <= 4:
            continue
        base = symbol[:-4]
        if base in STABLE_BASES:
            continue
        turnover = _safe_float(item.get("turnover24h"))
        if turnover <= 0:
            continue
        candidates.append((turnover, symbol))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    symbols = [symbol for _, symbol in candidates[:limit]]
    btc_available = any(symbol == "BTCUSDC" for _, symbol in candidates)
    if not btc_available:
        raise RuntimeError("BTCUSDC is unavailable from Bybit EU USDC spot tickers")
    if "BTCUSDC" not in symbols:
        if len(symbols) >= limit:
            symbols[-1] = "BTCUSDC"
        else:
            symbols.append("BTCUSDC")
    return list(dict.fromkeys(symbols))


async def _fetch_symbol_metrics(
    client: httpx.AsyncClient,
    symbol: str,
    semaphore: asyncio.Semaphore,
    now_ms: int,
) -> dict[str, Any]:
    async with semaphore:
        payload = await _bybit_get(
            client,
            "/v5/market/kline",
            {"category": "spot", "symbol": symbol, "interval": "D", "limit": 120},
        )
    rows = list((payload.get("result") or {}).get("list") or [])
    bars = parse_closed_daily_klines(rows, now_ms=now_ms)
    return compute_symbol_metrics(symbol, bars)


async def build_current_snapshot() -> dict[str, Any]:
    captured_at = datetime.now(timezone.utc)
    now_ms = int(captured_at.timestamp() * 1000)
    headers = {"User-Agent": "bybit-eu-relative-strength-shadow/1"}
    async with httpx.AsyncClient(headers=headers) as client:
        ticker_payload = await _bybit_get(
            client, "/v5/market/tickers", {"category": "spot"}
        )
        symbols = _select_universe(list(ticker_payload["result"]["list"]))
        semaphore = asyncio.Semaphore(5)
        results = await asyncio.gather(
            *(
                _fetch_symbol_metrics(client, symbol, semaphore, now_ms)
                for symbol in symbols
            ),
            return_exceptions=True,
        )

    analyses: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for symbol, result in zip(symbols, results):
        if isinstance(result, Exception):
            failures.append(
                {
                    "symbol": symbol,
                    "error_type": type(result).__name__,
                    "error": str(result)[:300],
                }
            )
        else:
            analyses.append(result)

    if not any(item.get("symbol") == "BTCUSDC" for item in analyses):
        raise RuntimeError("BTCUSDC relative-strength analysis failed")
    if len(analyses) < MIN_ANALYZED_SYMBOLS:
        raise RuntimeError(
            f"insufficient relative-strength universe coverage: {len(analyses)}/{len(symbols)}"
        )

    snapshot = build_snapshot(analyses, captured_at=captured_at)
    snapshot["requested_symbols"] = symbols
    snapshot["analyzed_symbols"] = [item["symbol"] for item in analyses]
    snapshot["failed_symbols"] = failures
    snapshot["coverage_pct"] = len(analyses) / len(symbols) * 100.0 if symbols else 0.0
    snapshot["source"] = "Bybit EU public USDC spot tickers + completed 1D OHLCV"
    return snapshot


async def persist_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    captured_at = datetime.fromisoformat(
        str(snapshot["captured_at"]).replace("Z", "+00:00")
    )
    captured_day = captured_at.astimezone(timezone.utc).date()
    source_sha = os.getenv("RAILWAY_GIT_COMMIT_SHA") or None
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        await connection.execute(SCHEMA_SQL)
        history = await append_snapshot_history(
            connection,
            research_family="relative-strength",
            spec_version=SPEC_VERSION,
            captured_at=captured_at,
            capture_bucket=captured_day,
            source_commit_sha=source_sha,
            snapshot=snapshot,
        )
        snapshot["immutable_history"] = history
        await connection.execute(
            """
            INSERT INTO research_relative_strength_snapshots (
                spec_version,captured_day,captured_at,source_commit_sha,
                universe_size,payload,updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6::jsonb,NOW())
            ON CONFLICT (spec_version,captured_day) DO UPDATE SET
                captured_at=EXCLUDED.captured_at,
                source_commit_sha=EXCLUDED.source_commit_sha,
                universe_size=EXCLUDED.universe_size,
                payload=EXCLUDED.payload,
                updated_at=NOW()
            """,
            SPEC_VERSION,
            captured_day,
            captured_at,
            source_sha,
            int(snapshot["universe_size"]),
            json.dumps(snapshot, separators=(",", ":")),
        )
    finally:
        await connection.close()
    return {
        **snapshot,
        "persisted": True,
        "captured_day": captured_day.isoformat(),
        "source_commit_sha": source_sha,
    }


async def capture_current_snapshot() -> dict[str, Any]:
    return await persist_snapshot(await build_current_snapshot())


def _decode(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    return {}


async def status_payload() -> dict[str, Any]:
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        await connection.execute(SCHEMA_SQL)
        latest = await connection.fetchrow(
            """
            SELECT captured_day,captured_at,source_commit_sha,universe_size,payload
            FROM research_relative_strength_snapshots
            WHERE spec_version=$1
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            SPEC_VERSION,
        )
        total = await connection.fetchval(
            "SELECT COUNT(*)::int FROM research_relative_strength_snapshots WHERE spec_version=$1",
            SPEC_VERSION,
        )
    finally:
        await connection.close()

    latest_payload = None
    if latest:
        latest_payload = _decode(latest["payload"])
        latest_payload["captured_day"] = latest["captured_day"].isoformat()
        latest_payload["source_commit_sha"] = latest["source_commit_sha"]
    return {
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "spec": spec(),
        "snapshot_count": int(total or 0),
        "latest": latest_payload,
    }


def attach_relative_strength_research(
    app: FastAPI, require_api_key: Callable[..., Any]
) -> None:
    @app.get(
        "/v1/research/relative-strength/spec",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def relative_strength_spec() -> dict[str, Any]:
        return spec()

    @app.post(
        "/v1/research/relative-strength/capture",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def relative_strength_capture() -> dict[str, Any]:
        try:
            return await capture_current_snapshot()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research relative-strength capture unavailable: {type(exc).__name__}",
            ) from exc

    @app.get(
        "/v1/research/relative-strength/status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def relative_strength_status() -> dict[str, Any]:
        try:
            return await status_payload()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research relative-strength status unavailable: {type(exc).__name__}",
            ) from exc
