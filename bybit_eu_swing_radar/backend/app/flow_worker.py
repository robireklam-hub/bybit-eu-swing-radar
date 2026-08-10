"""Bybit EU Trading Radar v0.7.2.2 — OI/funding flow worker.

Railway start command:
    python flow_worker.py

This worker is intentionally separate from the v0.7.2 day-trade strategy.
It reads fresh cached USDC spot setups and writes derivatives context caches.
It does not change setup classification, scores, triggers, stops, targets,
target paths, journal/backtest state, or Bybit EU shortability.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx

from flow_context import (
    DERIVATIVES_SCOPE,
    FEATURE_VERSION,
    STRATEGY_VERSION,
    build_context_payload,
    safe_float,
)


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return int(raw)


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return float(raw)


DATABASE_URL = os.getenv("DATABASE_URL", "")
DERIVATIVES_BYBIT_BASE_URL = os.getenv(
    "DERIVATIVES_BYBIT_BASE_URL", "https://api.bybit.com"
).rstrip("/")
FLOW_CONTEXT_ENABLED = env_bool("FLOW_CONTEXT_ENABLED", True)
FLOW_SETUP_MAX_AGE_MINUTES = env_int("FLOW_SETUP_MAX_AGE_MINUTES", 6)
FLOW_HTTP_CONCURRENCY = max(1, min(env_int("FLOW_HTTP_CONCURRENCY", 4), 8))
FLOW_MAX_SYMBOLS = max(1, min(env_int("FLOW_MAX_SYMBOLS", 30), 50))
FLOW_OI_HISTORY_LIMIT = max(50, min(env_int("FLOW_OI_HISTORY_LIMIT", 60), 200))
FLOW_PRICE_EPSILON_PCT = env_float("FLOW_PRICE_EPSILON_PCT", 0.10)
FLOW_OI_EPSILON_PCT = env_float("FLOW_OI_EPSILON_PCT", 0.25)


async def upsert_cache(
    connection: asyncpg.Connection,
    key: str,
    payload: dict[str, Any],
) -> None:
    await connection.execute(
        """
        INSERT INTO radar_cache (cache_key, payload, updated_at)
        VALUES ($1, $2::jsonb, NOW())
        ON CONFLICT (cache_key)
        DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
        """,
        key,
        json.dumps(payload, ensure_ascii=False, default=str),
    )


async def load_fresh_setups(connection: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await connection.fetch(
        """
        SELECT cache_key, payload, updated_at
        FROM radar_cache
        WHERE cache_key LIKE 'day_trade_setup:%'
          AND updated_at >= NOW() - ($1::int * INTERVAL '1 minute')
        ORDER BY updated_at DESC
        """,
        FLOW_SETUP_MAX_AGE_MINUTES,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            continue
        symbol = str(payload.get("symbol") or "").upper()
        if not symbol.endswith("USDC"):
            continue
        result.append(
            {
                "setup": payload,
                "updated_at": row["updated_at"],
            }
        )

    # Stable order: BTC first, then setup score descending.
    result.sort(
        key=lambda item: (
            0 if str(item["setup"].get("symbol")).upper() == "BTCUSDC" else 1,
            -float(item["setup"].get("setup_score") or 0.0),
            str(item["setup"].get("symbol") or ""),
        )
    )
    return result[:FLOW_MAX_SYMBOLS]


class BybitGlobalPublic:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.get(
            f"{DERIVATIVES_BYBIT_BASE_URL}{path}",
            params=params,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") != 0:
            raise RuntimeError(
                f"Bybit global error retCode={data.get('retCode')} retMsg={data.get('retMsg')}"
            )
        return data.get("result") or {}

    async def linear_instruments(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(5):
            params: dict[str, Any] = {"category": "linear", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            result = await self._get("/v5/market/instruments-info", params)
            rows.extend(result.get("list") or [])
            cursor = str(result.get("nextPageCursor") or "")
            if not cursor:
                break
        return rows

    async def linear_tickers(self) -> list[dict[str, Any]]:
        result = await self._get("/v5/market/tickers", {"category": "linear"})
        return result.get("list") or []

    async def open_interest_history(self, symbol: str) -> list[dict[str, Any]]:
        result = await self._get(
            "/v5/market/open-interest",
            {
                "category": "linear",
                "symbol": symbol,
                "intervalTime": "5min",
                "limit": FLOW_OI_HISTORY_LIMIT,
            },
        )
        return result.get("list") or []

    async def price_history(self, symbol: str) -> list[dict[str, Any]]:
        result = await self._get(
            "/v5/market/kline",
            {
                "category": "linear",
                "symbol": symbol,
                "interval": "5",
                "limit": FLOW_OI_HISTORY_LIMIT,
            },
        )
        converted: list[dict[str, Any]] = []
        for row in result.get("list") or []:
            if not isinstance(row, list) or len(row) < 5:
                continue
            converted.append(
                {
                    "timestamp": row[0],
                    "open": row[1],
                    "high": row[2],
                    "low": row[3],
                    "close": row[4],
                }
            )
        return converted


def choose_derivative_market(
    base_asset: str,
    instruments: list[dict[str, Any]],
    ticker_symbols: set[str],
) -> str | None:
    base_asset = base_asset.upper()
    candidates: list[tuple[int, str]] = []

    for item in instruments:
        if str(item.get("status") or "").lower() != "trading":
            continue
        if str(item.get("baseCoin") or "").upper() != base_asset:
            continue

        contract_type = str(item.get("contractType") or "")
        if "Perpetual" not in contract_type:
            continue

        symbol = str(item.get("symbol") or "").upper()
        if not symbol or symbol not in ticker_symbols:
            continue

        quote = str(item.get("quoteCoin") or "").upper()
        quote_rank = {"USDT": 0, "USDC": 1}.get(quote, 9)
        candidates.append((quote_rank, symbol))

    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


async def fetch_context_for_setup(
    bybit: BybitGlobalPublic,
    semaphore: asyncio.Semaphore,
    setup_record: dict[str, Any],
    instruments: list[dict[str, Any]],
    tickers_by_symbol: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    setup = setup_record["setup"]
    updated_at = setup_record["updated_at"]
    symbol = str(setup.get("symbol") or "").upper()
    base_asset = str(setup.get("base_asset") or symbol.removesuffix("USDC")).upper()

    derivative_symbol = choose_derivative_market(
        base_asset,
        instruments,
        set(tickers_by_symbol),
    )

    if derivative_symbol is None:
        return build_context_payload(
            setup=setup,
            cache_updated_at=updated_at,
            derivative_symbol=None,
            ticker=None,
            oi_history=[],
            kline_history=[],
            price_epsilon_pct=FLOW_PRICE_EPSILON_PCT,
            oi_epsilon_pct=FLOW_OI_EPSILON_PCT,
        )

    try:
        async with semaphore:
            oi_history, kline_history = await asyncio.gather(
                bybit.open_interest_history(derivative_symbol),
                bybit.price_history(derivative_symbol),
            )

        return build_context_payload(
            setup=setup,
            cache_updated_at=updated_at,
            derivative_symbol=derivative_symbol,
            ticker=tickers_by_symbol.get(derivative_symbol),
            oi_history=oi_history,
            kline_history=kline_history,
            price_epsilon_pct=FLOW_PRICE_EPSILON_PCT,
            oi_epsilon_pct=FLOW_OI_EPSILON_PCT,
        )
    except Exception as exc:
        return build_context_payload(
            setup=setup,
            cache_updated_at=updated_at,
            derivative_symbol=derivative_symbol,
            ticker=tickers_by_symbol.get(derivative_symbol),
            oi_history=[],
            kline_history=[],
            price_epsilon_pct=FLOW_PRICE_EPSILON_PCT,
            oi_epsilon_pct=FLOW_OI_EPSILON_PCT,
            error=str(exc)[:500],
        )


async def write_status(
    connection: asyncpg.Connection,
    *,
    status: str,
    started_at: datetime,
    contexts: list[dict[str, Any]],
    note: str | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    payload = {
        "strategy_version": STRATEGY_VERSION,
        "feature_version": FEATURE_VERSION,
        "generated_at": generated_at.isoformat(),
        "status": status,
        "processed_symbols": len(contexts),
        "good_symbols": sum(1 for item in contexts if item.get("data_quality") == "GOOD"),
        "partial_symbols": sum(1 for item in contexts if item.get("data_quality") == "PARTIAL"),
        "degraded_symbols": sum(1 for item in contexts if item.get("data_quality") == "DEGRADED"),
        "matched_derivatives_symbols": sum(
            1
            for item in contexts
            if (item.get("bybit_global_derivatives") or {}).get("matched")
        ),
        "unmatched_symbols": [
            item.get("symbol")
            for item in contexts
            if not (item.get("bybit_global_derivatives") or {}).get("matched")
        ],
        "context_only": True,
        "hard_gate": False,
        "derivatives_scope": DERIVATIVES_SCOPE,
        "duration_seconds": round(
            (generated_at - started_at).total_seconds(), 3
        ),
        "note": note,
    }
    await upsert_cache(connection, "day_trade_flow_status", payload)
    return payload


async def run() -> None:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    started_at = datetime.now(timezone.utc)
    connection = await asyncpg.connect(DATABASE_URL, timeout=30)
    try:
        if not FLOW_CONTEXT_ENABLED:
            status = await write_status(
                connection,
                status="DISABLED",
                started_at=started_at,
                contexts=[],
                note="FLOW_CONTEXT_ENABLED=false",
            )
            print(f"Flow worker complete: {json.dumps(status, default=str)}")
            return

        setups = await load_fresh_setups(connection)
        if not setups:
            status = await write_status(
                connection,
                status="NO_FRESH_DAY_SETUPS",
                started_at=started_at,
                contexts=[],
                note=(
                    f"No day_trade_setup:* cache newer than "
                    f"{FLOW_SETUP_MAX_AGE_MINUTES} minutes."
                ),
            )
            print(f"Flow worker complete: {json.dumps(status, default=str)}")
            return

        timeout = httpx.Timeout(30.0, connect=15.0)
        limits = httpx.Limits(max_connections=10, max_keepalive_connections=10)
        async with httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            headers={"User-Agent": "Bybit-EU-Trading-Radar-Flow/0.7.2.2"},
        ) as client:
            bybit = BybitGlobalPublic(client)
            instruments, tickers = await asyncio.gather(
                bybit.linear_instruments(),
                bybit.linear_tickers(),
            )
            tickers_by_symbol = {
                str(item.get("symbol") or "").upper(): item
                for item in tickers
                if item.get("symbol")
            }

            semaphore = asyncio.Semaphore(FLOW_HTTP_CONCURRENCY)
            contexts = await asyncio.gather(
                *(
                    fetch_context_for_setup(
                        bybit,
                        semaphore,
                        setup_record,
                        instruments,
                        tickers_by_symbol,
                    )
                    for setup_record in setups
                )
            )

        async with connection.transaction():
            for context in contexts:
                await upsert_cache(
                    connection,
                    f"day_trade_flow:{context['symbol']}",
                    context,
                )

            overall_status = (
                "OK"
                if contexts and all(item.get("data_quality") == "GOOD" for item in contexts)
                else "PARTIAL"
            )
            status = await write_status(
                connection,
                status=overall_status,
                started_at=started_at,
                contexts=contexts,
            )

        print(f"Flow worker complete: {json.dumps(status, default=str)}")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(run())
