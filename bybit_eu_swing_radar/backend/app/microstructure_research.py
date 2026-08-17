"""Attach the research-only microstructure recorder to the API process.

The recorder is isolated from live strategy/scoring/execution. A PostgreSQL
advisory lock prevents duplicate writers if more than one API process starts.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from fastapi import Depends, FastAPI

from research.microstructure.alignment import (
    alignment_spec,
    load_feature_rows,
    sample_readiness,
)
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


def _parse_dt(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("alignment boundary timestamp is missing")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("alignment boundary timestamp must be timezone-aware")
    return parsed


def build_alignment_status(
    readiness_payload: Mapping[str, Any],
    features: Iterable[Mapping[str, Any]],
    symbols: Iterable[str],
) -> dict[str, Any]:
    """Combine preregistered data-quality and sample gates without labels."""
    wanted = list(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    sample = sample_readiness(features, wanted)
    data_quality_ready = readiness_payload.get("ready_for_forward_feature_analysis") is True
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "label_blind": True,
        "post_signal_data_used": False,
        "promotion_allowed": False,
        "spec": alignment_spec(),
        "data_quality_ready": data_quality_ready,
        "sample": sample,
        "ready_for_preregistered_effect_test": (
            data_quality_ready and sample["ready_for_preregistered_effect_test"]
        ),
    }


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

    @app.get(
        "/v1/research/microstructure/alignment-status",
        dependencies=[Depends(require_api_key)],
    )
    async def alignment_status() -> dict[str, Any]:
        """Report label-blind preregistered alignment sample readiness only."""
        try:
            _ensure_task_started()
            await asyncio.sleep(0)
            recorder = _ensure_recorder()
            readiness_payload = await get_readiness(
                recorder.config.database_url,
                recorder.config.symbols,
                recorder.config.bucket_seconds,
            )
            if readiness_payload.get("ready_for_forward_feature_analysis") is not True:
                return build_alignment_status(
                    readiness_payload,
                    [],
                    recorder.config.symbols,
                )

            readiness_symbols = readiness_payload.get("symbols") or []
            first_bucket_values = [
                item.get("first_bucket_at")
                for item in readiness_symbols
                if isinstance(item, Mapping) and item.get("first_bucket_at")
            ]
            if len(first_bucket_values) != len(recorder.config.symbols):
                raise ValueError("readiness first_bucket_at coverage is incomplete")
            since = max(_parse_dt(value) for value in first_bucket_values)
            until = _parse_dt(readiness_payload.get("checked_at"))
            features = await load_feature_rows(
                recorder.config.database_url,
                recorder.config.symbols,
                since,
                until,
                bucket_seconds=recorder.config.bucket_seconds,
            )
            payload = build_alignment_status(
                readiness_payload,
                features,
                recorder.config.symbols,
            )
            payload["interval"] = {
                "since": since.isoformat(),
                "until": until.isoformat(),
            }
            return payload
        except Exception as exc:
            logger.exception("microstructure alignment status query failed")
            return {
                "research_only": True,
                "live_strategy_mutated": False,
                "label_blind": True,
                "post_signal_data_used": False,
                "promotion_allowed": False,
                "spec": alignment_spec(),
                "ready_for_preregistered_effect_test": False,
                "error_type": type(exc).__name__,
                "error": str(exc)[:1000],
            }
