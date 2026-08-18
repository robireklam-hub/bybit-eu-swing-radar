"""Read-only API for standalone v0.7.3 prospective funnel research status."""
from __future__ import annotations

from fastapi import Depends, HTTPException

from app.repository import RadarRepository

CACHE_KEY = "day_trade_prospective_funnel_status"


def attach_prospective_funnel_research(app, require_api_key) -> None:
    repo = RadarRepository()

    @app.get(
        "/v1/day-trade/research/prospective-funnel/status",
        dependencies=[Depends(require_api_key)],
    )
    async def prospective_funnel_status():
        result = await repo.get_cache(CACHE_KEY)
        if result is None:
            raise HTTPException(
                status_code=503,
                detail="No standalone prospective funnel capture yet.",
            )
        return result
