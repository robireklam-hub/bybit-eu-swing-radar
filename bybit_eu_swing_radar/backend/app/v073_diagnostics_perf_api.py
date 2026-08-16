"""Operational status and reusable research attachments for v0.7.3 diagnostics."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, FastAPI

from app.microstructure_research import attach_microstructure_research
from app.v073_research_dataset_api import attach_v073_research_dataset_routes
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

    # Reusable historical dataset plumbing remains available; exhausted tuning
    # surfaces are retired. The new recorder collects research data only and is
    # explicitly isolated from live day_worker strategy/scoring/execution.
    attach_v073_research_dataset_routes(app, require_api_key)
    attach_microstructure_research(app, require_api_key)
