"""Operational status route for the v0.7.3 diagnostics performance layer."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, FastAPI

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
