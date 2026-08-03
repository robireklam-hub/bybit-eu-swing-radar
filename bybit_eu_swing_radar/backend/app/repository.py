from datetime import datetime, timezone
import json
import asyncpg

from app.config import settings
from app.models import MarketRegime, ScanResponse, Setup


class RadarRepository:
    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(settings.database_url)

    async def get_latest_scan(self, direction: str, limit: int, min_score: float) -> ScanResponse | None:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                """
                SELECT payload
                FROM radar_cache
                WHERE cache_key = 'latest_scan'
                ORDER BY updated_at DESC
                LIMIT 1
                """
            )
        finally:
            await conn.close()
        if not row:
            return None

        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        response = ScanResponse.model_validate(payload)

        if direction == "long":
            response.shorts = []
        elif direction == "short":
            response.longs = []

        response.longs = [x for x in response.longs if x.setup_score >= min_score][:limit]
        response.shorts = [x for x in response.shorts if x.setup_score >= min_score][:limit]
        return response

    async def get_setup(self, symbol: str) -> Setup | None:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                """
                SELECT payload
                FROM radar_cache
                WHERE cache_key = $1
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                f"setup:{symbol.upper()}",
            )
        finally:
            await conn.close()
        if not row:
            return None
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return Setup.model_validate(payload)

    async def get_regime(self) -> MarketRegime | None:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                """
                SELECT payload
                FROM radar_cache
                WHERE cache_key = 'market_regime'
                ORDER BY updated_at DESC
                LIMIT 1
                """
            )
        finally:
            await conn.close()
        if not row:
            return None
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return MarketRegime.model_validate(payload)
