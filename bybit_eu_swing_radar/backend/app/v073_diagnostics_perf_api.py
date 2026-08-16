"""Operational status and read-only research routes for v0.7.3 diagnostics."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, FastAPI

from app.v073_research_dataset_api import attach_v073_research_dataset_routes
from app.v073_research_flow_v2_api import attach_v073_research_flow_v2_routes
from app.v073_sensitivity_api import attach_v073_sensitivity_routes
from app.v073_structure_ab_api import attach_v073_structure_ab_routes
from app.v073_target_path_ab_api import attach_v073_target_path_ab_routes
from diagnostics_v073 import STRATEGY_VERSION
from diagnostics_v073_perf import get_runtime_progress, install_performance_patch

install_performance_patch()

def attach_v073_diagnostic_perf_routes(
    app: FastAPI,
    require_api_key: Callable[..., Any],
) -> None:
    @app.get(
        "/v1/day-trade/backtest/diagnostics/v073/runtime-status",
        dependencies=[Depends(require_api_key)],
    )
    async def runtime_status() -> dict[str, Any]:
        return {
            "strategy_version": STRATEGY_VERSION,
            "performance_patch": "v073-diagnostics-performance-1",
            "runtime_progress": get_runtime_progress(),
        }

    # Attach read-only sensitivity through the existing diagnostics hook so live
    # day-trade strategy/scoring/execution modules remain untouched.
    attach_v073_sensitivity_routes(app, require_api_key)

    # Attach the completed single-hypothesis pivot-structure A/B runner.
    attach_v073_structure_ab_routes(app, require_api_key)

    # Attach the isolated structural target-path CURRENT/FRESH/IGNORE replay.
    # It reuses research tables/batching and never patches live strategy state.
    attach_v073_target_path_ab_routes(app, require_api_key)

    # Attach the materialized opportunity-level research dataset. It reuses the
    # diagnostic replay but does not alter live day-trade logic.
    attach_v073_research_dataset_routes(app, require_api_key)

    # Attach historical derivatives enrichment as a separate research-only path.
    # OI/funding remains contextual and never becomes a live hard gate.
    attach_v073_research_flow_v2_routes(app, require_api_key)
