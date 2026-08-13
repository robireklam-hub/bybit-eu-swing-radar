"""Cached OI/funding flow worker for Trading Radar v0.7.2.2.

Railway cron start command:
    python flow_worker.py

This worker is isolated from day_worker.py/backtest.py and does not modify strategy
logic. It reads fresh day_trade_setup caches and adds day_trade_flow:<SYMBOL> caches.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import httpx

from flow_context import build_flow_payload, safe_float


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid integer environment variable {name}={raw!r}") from exc


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid float environment variable {name}={raw!r}") from exc


BUDAPEST = ZoneInfo("Europe/Budapest")

DATABASE_URL = os.getenv("DATABASE_URL", "")
DERIVATIVES_BYBIT_BASE_URL = os.getenv(
    "DERIVATIVES_BYBIT_BASE_URL", "https://api.bybit.com"
).rstrip("/")
FLOW_CONTEXT_ENABLED = env_bool("FLOW_CONTEXT_ENABLED", True)
FLOW_SETUP_MAX_AGE_MINUTES = min(max(env_int("FLOW_SETUP_MAX_AGE_MINUTES", 6), 5), 120)
FLOW_HTTP_CONCURRENCY = min(max(env_int("FLOW_HTTP_CONCURRENCY", 4), 1), 8)
FLOW_MAX_SYMBOLS = min(max(env_int("FLOW_MAX_SYMBOLS", 30), 3), 60)
FLOW_OI_HISTORY_LIMIT = min(max(env_int("FLOW_OI_HISTORY_LIMIT", 60), 50), 200)
FLOW_PRICE_EPSILON_PCT = max(env_float("FLOW_PRICE_EPSILON_PCT", 0.10), 0.0)
FLOW_OI_EPSILON_PCT = max(env_float("FLOW_OI_EPSILON_PCT", 0.25), 0.0)


class BybitDerivativesAPI:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        response = await self.client.get(
            f"{DERIVATIVES_BYBIT_BASE_URL}{path}", params=params
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("retCode") != 0:
            raise RuntimeError(
                f"Bybit global API error {payload.get('retCode')}: {payload.get('retMsg')}"
            )
        return payload

    async def linear_instruments(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(5):
            params: dict[str, Any] = {"category": "linear", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            payload = await self.get("/v5/market/instruments-info", **params)
            result = payload.get("result") or {}
            rows.extend(result.get("list") or [])
            cursor = str(result.get("nextPageCursor") or "")
            if not cursor:
                break
        return rows

    async def linear_tickers(self) -> list[dict[str, Any]]:
        payload = await self.get("/v5/market/tickers", category="linear")
        return (payload.get("result") or {}).get("list") or []

    async def open_interest_history(self, symbol: str) -> list[dict[str, Any]]:
        payload = await self.get(
            "/v5/market/open-interest",
            category="linear",
            symbol=symbol,
            intervalTime="5min",
            limit=FLOW_OI_HISTORY_LIMIT,
        )
        return (payload.get("result") or {}).get("list") or []


def decode_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    return {}


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
    setups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        payload = decode_payload(row["payload"])
        symbol = str(payload.get("symbol") or str(row["cache_key"]).split(":", 1)[-1]).upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        payload["symbol"] = symbol
        payload["_cache_updated_at"] = row["updated_at"].isoformat()
        setups.append(payload)
    # Stable priority for the main majors, then the rest by setup score.
    major_rank = {
        "BTCUSDC": 0, "ETHUSDC": 1, "SOLUSDC": 2, "XRPUSDC": 3,
        "DOGEUSDC": 4, "BNBUSDC": 5, "HYPEUSDC": 6, "ADAUSDC": 7,
        "LINKUSDC": 8, "SUIUSDC": 9,
    }
    setups.sort(
        key=lambda x: (
            major_rank.get(str(x.get("symbol")), 100),
            -safe_float(x.get("setup_score"), 0.0),
            str(x.get("symbol")),
        )
    )
    return setups[:FLOW_MAX_SYMBOLS]


def choose_derivative_market(
    base: str,
    instruments: list[dict[str, Any]],
    ticker_map: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    candidates = []
    for item in instruments:
        if str(item.get("status")) != "Trading":
            continue
        if str(item.get("baseCoin", "")).upper() != base.upper():
            continue
        contract_type = str(item.get("contractType", ""))
        if "perpetual" not in contract_type.lower():
            continue
        symbol = str(item.get("symbol", "")).upper()
        ticker = ticker_map.get(symbol, {})
        turnover = safe_float(ticker.get("turnover24h"), 0.0)
        quote = str(item.get("quoteCoin", "")).upper()
        quote_rank = 2 if quote == "USDT" else 1 if quote == "USDC" else 0
        candidates.append((turnover, quote_rank, symbol, item))
    if not candidates:
        return None
    candidates.sort(reverse=True, key=lambda x: (x[0], x[1], x[2]))
    return candidates[0][3]


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
        json.dumps(payload, ensure_ascii=False),
    )


async def run_flow_worker() -> dict[str, Any]:
    flow_batch_id = str(uuid4())
    if not FLOW_CONTEXT_ENABLED:
        return {
            "enabled": False,
            "status": "DISABLED",
            "flow_batch_id": flow_batch_id,
            "symbols": [],
            "processed": 0,
            "good": 0,
            "partial": 0,
            "no_derivative_match": 0,
            "errors": [],
        }
    if not DATABASE_URL:
        raise RuntimeError(
            f"DATABASE_URL is not configured (flow_batch_id={flow_batch_id})"
        )

    started = datetime.now(timezone.utc)
    timeout = httpx.Timeout(30.0, connect=10.0)
    limits = httpx.Limits(max_connections=FLOW_HTTP_CONCURRENCY)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        api = BybitDerivativesAPI(client)
        connection = await asyncpg.connect(DATABASE_URL, timeout=30)
        try:
            setups = await load_fresh_setups(connection)
            if not setups:
                status = {
                    "strategy_mode": "DAY_TRADE",
                    "feature_version": "0.7.2.2",
                    "status": "NO_FRESH_DAY_SETUPS",
                    "data_as_of": started.isoformat(),
                    "data_as_of_budapest": started.astimezone(BUDAPEST).isoformat(),
                    "processed": 0,
                    "symbols": [],
                    "flow_batch_id": flow_batch_id,
                    "errors": [],
                }
                await upsert_cache(connection, "day_trade_flow_status", status)
                return status

            instruments, tickers = await asyncio.gather(
                api.linear_instruments(), api.linear_tickers()
            )
            ticker_map = {str(x.get("symbol", "")).upper(): x for x in tickers}
            semaphore = asyncio.Semaphore(FLOW_HTTP_CONCURRENCY)

            async def process(setup: dict[str, Any]) -> tuple[str, dict[str, Any], str | None]:
                spot_symbol = str(setup.get("symbol", "")).upper()
                base = str(setup.get("base_asset") or spot_symbol.removesuffix("USDC")).upper()
                instrument = choose_derivative_market(base, instruments, ticker_map)
                if instrument is None:
                    payload = build_flow_payload(
                        spot_symbol=spot_symbol,
                        setup_payload=setup,
                        derivative_instrument=None,
                        derivative_ticker=None,
                        oi_history=None,
                        generated_at=datetime.now(timezone.utc),
                        price_epsilon_pct=FLOW_PRICE_EPSILON_PCT,
                        oi_epsilon_pct=FLOW_OI_EPSILON_PCT,
                    )
                    return spot_symbol, payload, None
                derivative_symbol = str(instrument.get("symbol", "")).upper()
                try:
                    async with semaphore:
                        history = await api.open_interest_history(derivative_symbol)
                    payload = build_flow_payload(
                        spot_symbol=spot_symbol,
                        setup_payload=setup,
                        derivative_instrument=instrument,
                        derivative_ticker=ticker_map.get(derivative_symbol, {}),
                        oi_history=history,
                        generated_at=datetime.now(timezone.utc),
                        price_epsilon_pct=FLOW_PRICE_EPSILON_PCT,
                        oi_epsilon_pct=FLOW_OI_EPSILON_PCT,
                    )
                    return spot_symbol, payload, None
                except Exception as exc:
                    payload = build_flow_payload(
                        spot_symbol=spot_symbol,
                        setup_payload=setup,
                        derivative_instrument=instrument,
                        derivative_ticker=ticker_map.get(derivative_symbol, {}),
                        oi_history=None,
                        generated_at=datetime.now(timezone.utc),
                        price_epsilon_pct=FLOW_PRICE_EPSILON_PCT,
                        oi_epsilon_pct=FLOW_OI_EPSILON_PCT,
                    )
                    payload["coverage_status"] = "PARTIAL_OI_HISTORY_ERROR"
                    return spot_symbol, payload, str(exc)

            processed = await asyncio.gather(*(process(setup) for setup in setups))
            errors = []
            good = 0
            partial = 0
            no_match = 0
            async with connection.transaction():
                for symbol, payload, error in processed:
                    payload["flow_batch_id"] = flow_batch_id
                    await upsert_cache(connection, f"day_trade_flow:{symbol}", payload)
                    coverage = str(payload.get("coverage_status"))
                    if coverage == "GOOD":
                        good += 1
                    elif coverage == "NO_BYBIT_GLOBAL_LINEAR_PERPETUAL_MATCH":
                        no_match += 1
                    else:
                        partial += 1
                    if error:
                        errors.append({"symbol": symbol, "error": error})
                finished = datetime.now(timezone.utc)
                status = {
                    "strategy_mode": "DAY_TRADE",
                    "strategy_version": "0.7.2",
                    "feature_version": "0.7.2.2",
                    "status": "OK" if not errors else "PARTIAL",
                    "data_as_of": finished.isoformat(),
                    "data_as_of_budapest": finished.astimezone(BUDAPEST).isoformat(),
                    "source": "Bybit global public linear derivatives + cached Coinalyze secondary context",
                    "processed": len(processed),
                    "symbols": [symbol for symbol, _, _ in processed],
                    "flow_batch_id": flow_batch_id,
                    "good": good,
                    "partial": partial,
                    "no_derivative_match": no_match,
                    "errors": errors,
                    "duration_seconds": round((finished - started).total_seconds(), 2),
                    "notes": [
                        "Context-only feature: no v0.7.2 strategy gates are changed.",
                        "Bybit global derivatives are not Bybit EU spot execution data.",
                    ],
                }
                await upsert_cache(connection, "day_trade_flow_status", status)
            return status
        finally:
            await connection.close()


async def main() -> None:
    result = await run_flow_worker()
    print("Flow worker complete:", json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())
