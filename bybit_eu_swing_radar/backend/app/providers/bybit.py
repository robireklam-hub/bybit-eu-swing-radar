from typing import Any
import httpx

from app.config import settings


class BybitClient:
    def __init__(self) -> None:
        self.base_url = settings.bybit_base_url.rstrip("/")

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{self.base_url}{path}", params=params)
            response.raise_for_status()
            payload = response.json()
        if payload.get("retCode") != 0:
            raise RuntimeError(f"Bybit error: {payload.get('retCode')} {payload.get('retMsg')}")
        return payload

    async def server_time(self) -> dict[str, Any]:
        return await self._get("/v5/market/time")

    async def instruments(self, category: str = "spot") -> dict[str, Any]:
        return await self._get("/v5/market/instruments-info", {"category": category, "limit": 1000})

    async def tickers(self, category: str = "spot") -> dict[str, Any]:
        return await self._get("/v5/market/tickers", {"category": category})

    async def kline(self, symbol: str, interval: str, limit: int = 300, category: str = "spot") -> dict[str, Any]:
        return await self._get(
            "/v5/market/kline",
            {"category": category, "symbol": symbol.upper(), "interval": interval, "limit": limit},
        )

    async def orderbook(self, symbol: str, limit: int = 50, category: str = "spot") -> dict[str, Any]:
        return await self._get(
            "/v5/market/orderbook",
            {"category": category, "symbol": symbol.upper(), "limit": limit},
        )
