from pathlib import Path

import pytest

from research.microstructure import controlled_pullback_runtime_v2 as runtime
from research.microstructure import standalone


class FakeConnection:
    def __init__(self):
        self.closed = False

    def is_closed(self):
        return self.closed

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_periodic_runner_uses_dedicated_connection_and_publishes_success(monkeypatch):
    connection = FakeConnection()
    stop = __import__("asyncio").Event()
    status = {}

    async def connect(_url):
        return connection

    async def cycle(received_connection):
        assert received_connection is connection
        stop.set()
        return {
            "bucket_rows": 42,
            "candidate_records": 1,
            "inserted_records": 1,
            "outcome_visible": False,
            "promotion_allowed": False,
            "live_strategy_mutation": False,
        }

    monkeypatch.setattr(runtime.asyncpg, "connect", connect)
    monkeypatch.setattr(runtime, "run_prospective_cycle", cycle)

    await runtime.run_periodic_prospective_collection(
        "postgresql://research",
        stop,
        status,
        interval_seconds=0.01,
    )

    assert status["status"] == "ok"
    assert status["bucket_rows"] == 42
    assert status["candidate_records"] == 1
    assert status["outcome_visible"] is False
    assert status["promotion_allowed"] is False
    assert status["live_strategy_mutation"] is False
    assert connection.closed is True


@pytest.mark.asyncio
async def test_periodic_runner_surfaces_research_failure_without_raising(monkeypatch):
    connection = FakeConnection()
    stop = __import__("asyncio").Event()
    status = {}

    async def connect(_url):
        return connection

    async def cycle(_connection):
        stop.set()
        raise RuntimeError("research detector unavailable")

    monkeypatch.setattr(runtime.asyncpg, "connect", connect)
    monkeypatch.setattr(runtime, "run_prospective_cycle", cycle)

    await runtime.run_periodic_prospective_collection(
        "postgresql://research",
        stop,
        status,
        interval_seconds=0.01,
    )

    assert status["status"] == "degraded"
    assert "research detector unavailable" in status["last_error"]
    assert status["outcome_visible"] is False
    assert status["promotion_allowed"] is False
    assert status["live_strategy_mutation"] is False
    assert connection.closed is True


@pytest.mark.asyncio
async def test_recorder_heartbeat_embeds_prospective_status(monkeypatch):
    captured = {}

    class Recorder:
        def status(self):
            return {"running": True, "research_only": True}

    class Config:
        database_url = "postgresql://configured"

    async def persist(_url, payload, **metadata):
        captured["payload"] = payload
        captured["metadata"] = metadata

    monkeypatch.setattr(standalone, "persist_runtime_status", persist)
    monkeypatch.setattr(standalone, "_service_metadata", lambda: ("sha", "svc", "microstructure"))

    await standalone._persist(
        Recorder(),
        Config(),
        {"status": "ok", "bucket_rows": 99, "candidate_records": 0},
    )

    nested = captured["payload"]["controlled_pullback_v2"]
    assert nested["status"] == "ok"
    assert nested["bucket_rows"] == 99
    assert captured["metadata"]["source_commit_sha"] == "sha"


def test_standalone_wiring_is_research_only_and_separate_from_recorder_connection():
    source = Path("research/microstructure/standalone.py").read_text()
    assert "run_periodic_prospective_collection" in source
    assert 'name="controlled-pullback-v2-prospective"' in source
    assert '"controlled_pullback_v2"' in source

    runtime_source = Path("research/microstructure/controlled_pullback_runtime_v2.py").read_text()
    assert '"connection_policy": "DEDICATED_RESEARCH_DB_CONNECTION"' in runtime_source
    assert '"outcome_fields_read": False' in runtime_source
    assert '"live_strategy_mutation": False' in runtime_source
