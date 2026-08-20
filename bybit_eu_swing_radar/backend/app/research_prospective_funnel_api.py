"""Read-only API composition for standalone prospective research status routes."""
from __future__ import annotations

from fastapi import Depends, HTTPException

from app.repository import RadarRepository
from app.research_microstructure_alignment_v2_api import (
    attach_microstructure_alignment_v2_research,
)
from app.research_microstructure_alignment_v3_api import (
    attach_microstructure_alignment_v3_research,
)

CACHE_KEY = "day_trade_prospective_funnel_status"


def attach_prospective_funnel_research(app, require_api_key) -> None:
    # Keep independently preregistered microstructure cohorts observable through
    # the same research-only API composition point. These routes are read-only
    # and have no live strategy/scoring/eligibility/execution path.
    attach_microstructure_alignment_v2_research(app, require_api_key)
    attach_microstructure_alignment_v3_research(app, require_api_key)

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
