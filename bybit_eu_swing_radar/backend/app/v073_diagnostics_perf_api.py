"""Operational status and read-only research routes for v0.7.3 diagnostics."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, FastAPI

from app.v073_research_breakout_v5_api import attach_v073_research_breakout_v5_routes
from app.v073_research_dataset_api import attach_v073_research_dataset_routes
from app.v073_research_entry_v4_api import attach_v073_research_entry_v4_routes
from app.v073_research_flow_v2_api import attach_v073_research_flow_v2_routes
from app.v073_research_premium_v3_api import attach_v073_research_premium_v3_routes
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

    attach_v073_sensitivity_routes(app, require_api_key)
    attach_v073_structure_ab_routes(app, require_api_key)
    attach_v073_target_path_ab_routes(app, require_api_key)
    attach_v073_research_dataset_routes(app, require_api_key)
    attach_v073_research_flow_v2_routes(app, require_api_key)
    attach_v073_research_premium_v3_routes(app, require_api_key)
    attach_v073_research_entry_v4_routes(app, require_api_key)

    # Separate continuation-family research after the sweep family failed.
    # This endpoint remains research-only and cannot mutate live strategy state.
    attach_v073_research_breakout_v5_routes(app, require_api_key)
