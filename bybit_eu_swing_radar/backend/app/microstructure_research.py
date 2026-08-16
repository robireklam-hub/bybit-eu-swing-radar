"""Attach the research-only microstructure recorder to the API process.

The recorder is isolated from live strategy/scoring/execution. A PostgreSQL
advisory lock prevents duplicate writers if more than one API process starts.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from fastapi import Depends, FastAPI

from research.microstructure.collector import MicrostructureConfig, MicrostructureRecorder
from research.microstructure.readiness import get_readiness

logger = logging.getLogger(__name__)

_config: MicrostructureConfig | None = None
_recorder: MicrostructureRecorder | None = None
_task: asyncio.Task[None] | None = None


def _ensure_recorder() -> MicrostructureRecorder:
    global _config, _recorder
    if _recorder is None:
        _config = MicrostructureConfig.from_env()
        _recorder = MicrostructureRecorder(_config)
    return _recorder


def _ensure_task_started() -> bool:
    """Start the research collector on the current event loop when needed.

    Railway's API process is allowed to run with application lifespan hooks
    disabled, so the collector must not depend exclusively on FastAPI startup
    events. The authenticated research status endpoint calls this helper as a
    fail-safe. It only starts research data collection and never mutates live
    strategy/scoring/execution state.
    """
    global _task
    recorder = _ensure_recorder()
    if not recorder.config.enabled:
        return False
    if _task is not None and not _task.done():
        return False
    _task = asyncio.create_task(recorder.run(), name="microstructure-recorder-v1")
    logger.info(
        "microstructure recorder task started: symbols=%s bucket=%ss depth=%s",
        recorder.config.symbols,
        recorder.config.bucket_seconds,
        recorder.config.depth,
    )
    return True


async def _startup() -> None:
    _ensure_task_started()


async def _shutdown() -> None:
    global _task
    if _recorder is not None:
        await _recorder.stop()
    if _task is not None and not _task.done():
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
    _task = None


def microstructure_status() -> dict[str, Any]:
    try:
        recorder = _ensure_recorder()
    except Exception as exc:
        return {
            "research_only": True,
            "live_strategy_mutated": False,
            "enabled": False,
            "running": False,
            "configuration_error": str(exc),
        }
    return recorder.status()


def attach_microstructure_research(
    app: FastAPI,
    require_api_key: Callable[..., Any],
) -> None:
    # Keep normal FastAPI lifecycle registration when available. The status
    # route below is also a self-start fail-safe because Railway may run the API
    # with lifespan hooks disabled.
    add_event_handler = getattr(app, "add_event_handler", None)
    if callable(add_event_handler):
        add_event_handler("startup", _startup)
        add_event_handler("shutdown", _shutdown)

    @app.get(
        "/v1/research/microstructure/status",
        dependencies=[Depends(require_api_key)],
    )
    async def status() -> dict[str, Any]:
        try:
            _ensure_task_started()
            # Yield once so the newly scheduled collector can set its initial
            # runtime state before we report status on first access.
            await asyncio.sleep(0)
        except Exception as exc:
            logger.exception("microstructure recorder self-start failed")
            payload = microstructure_status()
            payload["start_error"] = str(exc)[:1000]
            return payload
        return microstructure_status()

    @app.get(
        "/v1/research/microstructure/readiness",
        dependencies=[Depends(require_api_key)],
    )
    async def readiness() -> dict[str, Any]:
        """Measure forward dataset quality without testing or tuning an edge."""
        try:
            _ensure_task_started()
            await asyncio.sleep(0)
            recorder = _ensure_recorder()
            return await get_readiness(
                recorder.config.database_url,
                recorder.config.symbols,
                recorder.config.bucket_seconds,
            )
        except Exception as exc:
            logger.exception("microstructure readiness query failed")
            return {
                "research_only": True,
                "live_strategy_mutated": False,
                "gate_version": "microstructure-readiness-v1",
                "ready_for_forward_feature_analysis": False,
                "promotion_allowed": False,
                "error_type": type(exc).__name__,
                "error": str(exc)[:1000],
            }
