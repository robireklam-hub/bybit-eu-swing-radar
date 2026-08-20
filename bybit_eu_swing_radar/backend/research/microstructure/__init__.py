"""Bybit EU spot microstructure collection and feature engineering.

Keep the package import lightweight. Research-only feature/calibration modules are
used by standalone production probes that intentionally do not require the database
collector dependency. Collector symbols remain available through lazy attribute
loading for backward compatibility.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "MicrostructureConfig",
    "MicrostructureRecorder",
    "OrderBookState",
    "ResearchBucket",
    "depth_metrics",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    from .collector import (
        MicrostructureConfig,
        MicrostructureRecorder,
        OrderBookState,
        ResearchBucket,
        depth_metrics,
    )

    exports = {
        "MicrostructureConfig": MicrostructureConfig,
        "MicrostructureRecorder": MicrostructureRecorder,
        "OrderBookState": OrderBookState,
        "ResearchBucket": ResearchBucket,
        "depth_metrics": depth_metrics,
    }
    return exports[name]
