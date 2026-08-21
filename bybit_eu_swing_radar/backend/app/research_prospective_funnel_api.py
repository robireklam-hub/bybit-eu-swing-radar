"""Read-only API composition for standalone prospective research status routes."""
from __future__ import annotations

from fastapi import Depends, HTTPException

from app.repository import RadarRepository
from app.research_microstructure_alignment_v2_api import attach_microstructure_alignment_v2_research
from app.research_microstructure_alignment_v3_api import attach_microstructure_alignment_v3_research
from app.research_microstructure_alignment_v4_api import attach_microstructure_alignment_v4_research
from app.research_microstructure_effect_v3_api import attach_microstructure_effect_v3_research

CACHE_KEY = "day_trade_prospective_funnel_status"
BARRIER_PARENT_CACHE_KEY = "day_barrier_clear_rearm_parent_status"
BARRIER_OBSERVER_CACHE_KEY = "day_barrier_clear_rearm_observer_status"


def attach_prospective_funnel_research(app, require_api_key) -> None:
    # Independently preregistered microstructure cohorts remain isolated and
    # research-only. V3 effect labels are fail-closed behind its frozen gate;
    # V4 remains label-blind until its own exact-cohort sample gate is ready.
    attach_microstructure_alignment_v2_research(app, require_api_key)
    attach_microstructure_alignment_v3_research(app, require_api_key)
    attach_microstructure_alignment_v4_research(app, require_api_key)
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
        "/v1/day-trade/research/barrier-clear-rearm/parent-status",
        dependencies=[Depends(require_api_key)],
    )
    async def barrier_clear_parent_status():
        result = await repo.get_cache(BARRIER_PARENT_CACHE_KEY)
        if result is None:
            raise HTTPException(status_code=503, detail="No prospective barrier-clear parent capture yet.")
        return result

    @app.get(
        "/v1/day-trade/research/barrier-clear-rearm/observer-status",
        dependencies=[Depends(require_api_key)],
    )
    async def barrier_clear_observer_status():
        result = await repo.get_cache(BARRIER_OBSERVER_CACHE_KEY)
        if result is None:
            raise HTTPException(status_code=503, detail="No prospective barrier-clear observation yet.")
        return result
