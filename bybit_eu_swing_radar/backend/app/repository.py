import json

import asyncpg

from app.config import settings
from app.models import MarketRegime, ScanResponse, Setup, WatchlistResponse


class RadarRepository:
    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(settings.database_url)

    async def get_cache(self, cache_key: str) -> dict | None:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                """
                SELECT payload
                FROM radar_cache
                WHERE cache_key = $1
                LIMIT 1
                """,
                cache_key,
            )
        finally:
            await conn.close()
        if not row:
            return None
        payload = row["payload"]
        return json.loads(payload) if isinstance(payload, str) else payload

    async def get_latest_scan(self, direction: str, limit: int, min_score: float) -> ScanResponse | None:
        payload = await self.get_cache("latest_scan")
        if payload is None:
            return None
        response = ScanResponse.model_validate(payload)
        if direction == "long":
            response.shorts = []
        elif direction == "short":
            response.longs = []
        response.longs = [x for x in response.longs if x.setup_score >= min_score][:limit]
        response.shorts = [x for x in response.shorts if x.setup_score >= min_score][:limit]
        return response

    async def get_setup(self, symbol: str) -> Setup | None:
        payload = await self.get_cache(f"setup:{symbol.upper()}")
        return Setup.model_validate(payload) if payload is not None else None

    async def get_regime(self) -> MarketRegime | None:
        payload = await self.get_cache("market_regime")
        return MarketRegime.model_validate(payload) if payload is not None else None

    async def get_watchlist(self, limit: int = 20) -> WatchlistResponse | None:
        payload = await self.get_cache("watchlist")
        if payload is None:
            return None
        response = WatchlistResponse.model_validate(payload)
        response.items = response.items[:limit]
        return response

    async def get_data_status(self) -> dict | None:
        return await self.get_cache("data_status")
