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


async def _startup() -> None:
    global _task
    recorder = _ensure_recorder()
    if not recorder.config.enabled:
        logger.info("microstructure recorder disabled")
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(recorder.run(), name="microstructure-recorder-v1")
        logger.info(
            "microstructure recorder task started: symbols=%s bucket=%ss depth=%s",
            recorder.config.symbols,
            recorder.config.bucket_seconds,
            recorder.config.depth,
        )


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
    # Production FastAPI exposes add_event_handler. A few repository tests use a
    # deliberately tiny FastAPI stub that only models route registration; keep
    # those import-only tests isolated from lifecycle behavior.
    add_event_handler = getattr(app, "add_event_handler", None)
    if callable(add_event_handler):
        add_event_handler("startup", _startup)
        add_event_handler("shutdown", _shutdown)

    @app.get(
        "/v1/research/microstructure/status",
        dependencies=[Depends(require_api_key)],
    )
    async def status() -> dict[str, Any]:
        return microstructure_status()
