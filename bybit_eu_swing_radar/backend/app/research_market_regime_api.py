"""Research-only forward market-regime shadow capture and status API."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable

import asyncpg
import httpx
from fastapi import Depends, FastAPI, HTTPException

from app.config import settings
from research.market_regime_shadow import (
    SPEC_VERSION,
    build_market_snapshot,
    classify_symbol,
    parse_bybit_klines,
    spec,
)

TRACKED_UNIVERSE_SIZE = 8
STABLE_BASES = {
    "USDC", "USDT", "DAI", "FDUSD", "TUSD", "USDE", "USDS", "PYUSD",
    "EUR", "EURC", "BUSD", "USD1", "RLUSD", "USDD", "USDQ",
}
INTERVAL_MS = {"240": 4 * 60 * 60 * 1000, "D": 24 * 60 * 60 * 1000}
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_market_regime_snapshots (
    spec_version TEXT NOT NULL,
    captured_hour TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    global_regime TEXT NOT NULL,
    dominant_direction TEXT NOT NULL,
    universe_size INTEGER NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (spec_version, captured_hour)
);
CREATE INDEX IF NOT EXISTS idx_research_market_regime_snapshots_time
ON research_market_regime_snapshots(captured_at DESC);
"""


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


async def _bybit_get(client: httpx.AsyncClient, path: str, params: dict[str, Any]) -> dict[str, Any]:
    response = await client.get(f"{settings.bybit_base_url.rstrip('/')}{path}", params=params, timeout=15.0)
    response.raise_for_status()
    payload = response.json()
    if int(payload.get("retCode", -1)) != 0:
        raise RuntimeError(f"Bybit public API error: {payload.get('retMsg')}")
    return payload


def _select_universe(tickers: list[dict[str, Any]], limit: int = TRACKED_UNIVERSE_SIZE) -> list[str]:
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
    candidates.sort(reverse=True)
    symbols = [symbol for _, symbol in candidates[:limit]]
    if "BTCUSDC" not in symbols:
        btc = next((symbol for _, symbol in candidates if symbol == "BTCUSDC"), None)
        if btc:
            if len(symbols) >= limit:
                symbols[-1] = btc
            else:
                symbols.append(btc)
    symbols = list(dict.fromkeys(symbols))
    if "BTCUSDC" not in symbols:
        raise RuntimeError("BTCUSDC is unavailable from Bybit EU USDC spot tickers")
    return symbols


async def _fetch_symbol_analysis(
    client: httpx.AsyncClient,
    symbol: str,
    semaphore: asyncio.Semaphore,
    now_ms: int,
) -> dict[str, Any]:
    async with semaphore:
        payload_4h, payload_1d = await asyncio.gather(
            _bybit_get(client, "/v5/market/kline", {"category": "spot", "symbol": symbol, "interval": "240", "limit": 140}),
            _bybit_get(client, "/v5/market/kline", {"category": "spot", "symbol": symbol, "interval": "D", "limit": 90}),
        )
    bars_4h = parse_bybit_klines(payload_4h["result"]["list"], interval_ms=INTERVAL_MS["240"], now_ms=now_ms)
    bars_1d = parse_bybit_klines(payload_1d["result"]["list"], interval_ms=INTERVAL_MS["D"], now_ms=now_ms)
    return classify_symbol(symbol, bars_4h, bars_1d)


async def build_current_snapshot() -> dict[str, Any]:
    captured_at = datetime.now(timezone.utc)
    now_ms = int(captured_at.timestamp() * 1000)
    async with httpx.AsyncClient(headers={"User-Agent": "bybit-eu-market-regime-shadow/1"}) as client:
        tickers_payload = await _bybit_get(client, "/v5/market/tickers", {"category": "spot"})
        symbols = _select_universe(list(tickers_payload["result"]["list"]))
        semaphore = asyncio.Semaphore(4)
        results = await asyncio.gather(
            *(_fetch_symbol_analysis(client, symbol, semaphore, now_ms) for symbol in symbols),
            return_exceptions=True,
        )

    analyses: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for symbol, result in zip(symbols, results):
        if isinstance(result, Exception):
            failures.append({"symbol": symbol, "error_type": type(result).__name__, "error": str(result)[:300]})
        else:
            analyses.append(result)
    if not any(item.get("symbol") == "BTCUSDC" for item in analyses):
        raise RuntimeError("BTCUSDC regime analysis failed")
    if len(analyses) < 5:
        raise RuntimeError(f"insufficient regime universe coverage: {len(analyses)}/{len(symbols)}")

    snapshot = build_market_snapshot(analyses, captured_at=captured_at)
    snapshot["requested_symbols"] = symbols
    snapshot["analyzed_symbols"] = [item["symbol"] for item in analyses]
    snapshot["failed_symbols"] = failures
    snapshot["coverage_pct"] = len(analyses) / len(symbols) * 100.0 if symbols else 0.0
    snapshot["source"] = "Bybit EU public spot OHLCV"
    return snapshot


async def persist_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    captured_at = datetime.fromisoformat(str(snapshot["captured_at"]).replace("Z", "+00:00"))
    captured_hour = captured_at.replace(minute=0, second=0, microsecond=0)
    source_sha = os.getenv("RAILWAY_GIT_COMMIT_SHA") or None
    connection = await asyncpg.connect(settings.database_url)
    try:
        await connection.execute(SCHEMA_SQL)
        await connection.execute(
            """
            INSERT INTO research_market_regime_snapshots (
                spec_version,captured_hour,captured_at,source_commit_sha,
                global_regime,dominant_direction,universe_size,payload,updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,NOW())
            ON CONFLICT (spec_version,captured_hour) DO UPDATE SET
                captured_at=EXCLUDED.captured_at,
                source_commit_sha=EXCLUDED.source_commit_sha,
                global_regime=EXCLUDED.global_regime,
                dominant_direction=EXCLUDED.dominant_direction,
                universe_size=EXCLUDED.universe_size,
                payload=EXCLUDED.payload,
                updated_at=NOW()
            """,
            SPEC_VERSION,
            captured_hour,
            captured_at,
            source_sha,
            snapshot["global_regime"],
            snapshot["dominant_direction"],
            int(snapshot["universe_size"]),
            json.dumps(snapshot, separators=(",", ":")),
        )
    finally:
        await connection.close()
    return {
        **snapshot,
        "persisted": True,
        "captured_hour": captured_hour.isoformat(),
        "source_commit_sha": source_sha,
    }


async def capture_current_snapshot() -> dict[str, Any]:
    return await persist_snapshot(await build_current_snapshot())


async def status_payload() -> dict[str, Any]:
    connection = await asyncpg.connect(settings.database_url)
    try:
        await connection.execute(SCHEMA_SQL)
        latest = await connection.fetchrow(
            """
            SELECT captured_at,captured_hour,source_commit_sha,global_regime,
                   dominant_direction,universe_size,payload
            FROM research_market_regime_snapshots
            WHERE spec_version=$1
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            SPEC_VERSION,
        )
        counts = await connection.fetch(
            """
            SELECT global_regime,COUNT(*)::int AS n
            FROM research_market_regime_snapshots
            WHERE spec_version=$1
            GROUP BY global_regime
            ORDER BY global_regime
            """,
            SPEC_VERSION,
        )
        total = await connection.fetchval(
            "SELECT COUNT(*)::int FROM research_market_regime_snapshots WHERE spec_version=$1",
            SPEC_VERSION,
        )
    finally:
        await connection.close()

    latest_payload: dict[str, Any] | None = None
    if latest:
        raw = latest["payload"]
        latest_payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
        latest_payload["captured_hour"] = latest["captured_hour"].isoformat()
        latest_payload["source_commit_sha"] = latest["source_commit_sha"]
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "label_free": True,
        "promotion_allowed": False,
        "spec": spec(),
        "snapshot_count": int(total or 0),
        "regime_counts": {row["global_regime"]: int(row["n"]) for row in counts},
        "latest": latest_payload,
    }


def attach_market_regime_research(app: FastAPI, require_api_key: Callable[..., Any]) -> None:
    @app.get(
        "/v1/research/market-regime/spec",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def market_regime_spec() -> dict[str, Any]:
        return spec()

    @app.post(
        "/v1/research/market-regime/capture",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def market_regime_capture() -> dict[str, Any]:
        try:
            return await capture_current_snapshot()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research market-regime capture unavailable: {type(exc).__name__}",
            ) from exc

    @app.get(
        "/v1/research/market-regime/status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def market_regime_status() -> dict[str, Any]:
        try:
            return await status_payload()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research market-regime status unavailable: {type(exc).__name__}",
            ) from exc
