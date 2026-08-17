from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException

from app.providers.bybit import BybitClient



def validate_usdc_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if (
        not normalized.endswith("USDC")
        or len(normalized) < 7
        or len(normalized) > 30
        or not normalized.isalnum()
    ):
        raise HTTPException(status_code=400, detail="Research orderbook requires a Bybit EU USDC spot symbol")
    return normalized


def compact_orderbook_payload(symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        raise RuntimeError("Bybit orderbook result is not an object")
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "execution_action": False,
        "source": "Bybit EU public spot orderbook",
        "symbol": symbol,
        "data_as_of": datetime.now(timezone.utc).isoformat(),
        "upstream_time_ms": payload.get("time"),
        "update_id": result.get("u"),
        "seq": result.get("seq"),
        "bids": result.get("b") or [],
        "asks": result.get("a") or [],
    }


def attach_research_swing_liquidity_routes(
    app: FastAPI,
    require_api_key: Callable[..., None],
    *,
    bybit_client: BybitClient | None = None,
) -> None:
    client = bybit_client or BybitClient()

    @app.get(
        "/v1/research/swing-liquidity/orderbook/{symbol}",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def research_swing_liquidity_orderbook(symbol: str) -> dict[str, Any]:
        normalized = validate_usdc_symbol(symbol)
        try:
            payload = await client.orderbook(normalized, limit=50, category="spot")
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Bybit EU public orderbook unavailable: {type(exc).__name__}",
            ) from exc
        return compact_orderbook_payload(normalized, payload)
