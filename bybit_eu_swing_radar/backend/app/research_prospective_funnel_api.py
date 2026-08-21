"""Read-only API composition for standalone prospective research status routes."""
from __future__ import annotations

from fastapi import Depends, HTTPException

from app.repository import RadarRepository
from app.research_microstructure_alignment_v2_api import attach_microstructure_alignment_v2_research
from app.research_microstructure_alignment_v3_api import attach_microstructure_alignment_v3_research
from app.research_microstructure_effect_v3_api import attach_microstructure_effect_v3_research

CACHE_KEY = "day_trade_prospective_funnel_status"


def attach_prospective_funnel_research(app, require_api_key) -> None:
    # Independently preregistered microstructure cohorts remain isolated and
    # research-only. V3 effect labels are fail-closed behind its frozen gate.
    attach_microstructure_alignment_v2_research(app, require_api_key)
    attach_microstructure_alignment_v3_research(app, require_api_key)
    attach_microstructure_effect_v3_research(app, require_api_key)

    repo = RadarRepository()

    @app.get(
        "/v1/day-trade/research/prospective-funnel/status",
        dependencies=[Depends(require_api_key)],
    )
    async def prospective_funnel_status():
        result = await repo.get_cache(CACHE_KEY)
        if result is None:
            raise HTTPException(status_code=503, detail="No standalone prospective funnel capture yet.")
        return result

    @app.get(
        "/v1/day-trade/research/barrier-clear-rearm/status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def barrier_clear_rearm_status():
        result = await repo.get_cache(CACHE_KEY)
        if result is None:
            raise HTTPException(status_code=503, detail="No standalone prospective research capture yet.")
        barrier = result.get("barrier_clear_rearm") if isinstance(result, dict) else None
        if not isinstance(barrier, dict):
            raise HTTPException(status_code=503, detail="No prospective barrier-clear recorder capture yet.")
        return barrier
