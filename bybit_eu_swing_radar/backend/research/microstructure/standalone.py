"""Standalone long-running microstructure recorder process for Railway.

The service retries the PostgreSQL advisory singleton lock until it becomes the
active writer. Runtime status is persisted separately so the API can report the
external collector state after cutover. Controlled-pullback v2 prospective
collection runs on a dedicated DB connection and cannot stop the recorder.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Any

from research.microstructure.collector import MicrostructureConfig, MicrostructureRecorder
from research.microstructure.controlled_pullback_runtime_v2 import (
    DEFAULT_INTERVAL_SECONDS,
    run_periodic_prospective_collection,
)
from research.microstructure.runtime_status import RECORDER_ID, persist_runtime_status

logger = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 5.0
LOCK_RETRY_SECONDS = 2.0
RESTART_RETRY_SECONDS = 5.0


def _service_metadata() -> tuple[str | None, str | None, str | None]:
    return (
        os.getenv("RAILWAY_GIT_COMMIT_SHA")
        or os.getenv("MICROSTRUCTURE_SOURCE_COMMIT_SHA")
        or None,
        os.getenv("RAILWAY_SERVICE_ID") or None,
        os.getenv("RAILWAY_SERVICE_NAME") or None,
    )


async def _persist(
    recorder: MicrostructureRecorder,
    config: MicrostructureConfig,
    prospective_status: dict[str, Any] | None = None,
) -> None:
    source_commit_sha, service_id, service_name = _service_metadata()
    payload: dict[str, Any] = {
        **recorder.status(),
        "process_role": "standalone",
        "recorder_id": RECORDER_ID,
        "controlled_pullback_v2": dict(prospective_status or {"status": "starting"}),
    }
    await persist_runtime_status(
        config.database_url,
        payload,
        source_commit_sha=source_commit_sha,
        service_id=service_id,
        service_name=service_name,
    )


async def run_standalone() -> None:
    config = MicrostructureConfig.from_env()
    if not config.enabled:
        raise RuntimeError("MICROSTRUCTURE_RECORDER_ENABLED must be true for standalone service")
    if not config.database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(name, stop.set)
        except NotImplementedError:
            pass

    prospective_status: dict[str, Any] = {"status": "starting"}
    prospective_task = asyncio.create_task(
        run_periodic_prospective_collection(
            config.database_url,
            stop,
            prospective_status,
            interval_seconds=float(
                os.getenv(
                    "MICROSTRUCTURE_CONTROLLED_PULLBACK_V2_INTERVAL_SECONDS",
                    str(DEFAULT_INTERVAL_SECONDS),
                )
            ),
        ),
        name="controlled-pullback-v2-prospective",
    )

    try:
        while not stop.is_set():
            recorder = MicrostructureRecorder(config)
            recorder_task = asyncio.create_task(recorder.run(), name="microstructure-recorder-standalone")
            try:
                while not stop.is_set() and not recorder_task.done():
                    await _persist(recorder, config, prospective_status)
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
                    except asyncio.TimeoutError:
                        pass

                if stop.is_set() and not recorder_task.done():
                    await recorder.stop()
                await asyncio.gather(recorder_task, return_exceptions=True)
                await _persist(recorder, config, prospective_status)
            except asyncio.CancelledError:
                await recorder.stop()
                recorder_task.cancel()
                await asyncio.gather(recorder_task, return_exceptions=True)
                raise
            except Exception:
                logger.exception("standalone microstructure recorder supervisor error")

            if stop.is_set():
                break
            delay = LOCK_RETRY_SECONDS if not recorder.runtime.singleton_acquired else RESTART_RETRY_SECONDS
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
    finally:
        stop.set()
        prospective_task.cancel()
        await asyncio.gather(prospective_task, return_exceptions=True)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_standalone())


if __name__ == "__main__":
    main()
