from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from research.microstructure import runtime_status
from research.microstructure.standalone import _service_metadata


class FakeConnection:
    def __init__(self, row):
        self.row = row
        self.closed = False

    async def fetchrow(self, *args, **kwargs):
        return self.row

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_runtime_status_marks_fresh_connected_external_recorder_healthy(monkeypatch):
    now = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
    row = {
        "heartbeat_at": now - timedelta(seconds=4),
        "source_commit_sha": "abc123",
        "service_id": "svc-1",
        "service_name": "microstructure-recorder",
        "payload": json.dumps({
            "enabled": True,
            "running": True,
            "singleton_acquired": True,
            "connected": True,
            "symbols": ["BTCUSDC", "ETHUSDC", "SOLUSDC"],
            "messages": 10,
            "rows_written": 3,
        }),
    }
    connection = FakeConnection(row)

    async def connect(_url):
        return connection

    monkeypatch.setattr(runtime_status.asyncpg, "connect", connect)
    result = await runtime_status.load_runtime_status(
        "postgresql://configured",
        ["BTCUSDC", "ETHUSDC", "SOLUSDC"],
        now=now,
    )
    assert result["process_role"] == "standalone"
    assert result["external_service_healthy"] is True
    assert result["heartbeat_age_seconds"] == pytest.approx(4.0)
    assert result["source_commit_sha"] == "abc123"
    assert connection.closed is True


@pytest.mark.asyncio
async def test_runtime_status_rejects_stale_heartbeat(monkeypatch):
    now = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
    row = {
        "heartbeat_at": now - timedelta(seconds=31),
        "source_commit_sha": "abc123",
        "service_id": "svc-1",
        "service_name": "microstructure-recorder",
        "payload": {
            "enabled": True,
            "running": True,
            "singleton_acquired": True,
            "connected": True,
            "symbols": ["BTCUSDC", "ETHUSDC", "SOLUSDC"],
        },
    }

    async def connect(_url):
        return FakeConnection(row)

    monkeypatch.setattr(runtime_status.asyncpg, "connect", connect)
    result = await runtime_status.load_runtime_status(
        "postgresql://configured",
        ["BTCUSDC", "ETHUSDC", "SOLUSDC"],
        now=now,
    )
    assert result["external_service_healthy"] is False
    assert result["status_reason"] != "ok"


def test_standalone_source_sha_falls_back_for_local_railway_upload(monkeypatch):
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.setenv("MICROSTRUCTURE_SOURCE_COMMIT_SHA", "expected-sha")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "svc")
    monkeypatch.setenv("RAILWAY_SERVICE_NAME", "microstructure-recorder")
    assert _service_metadata() == ("expected-sha", "svc", "microstructure-recorder")


def test_api_external_mode_contract_is_explicit_and_does_not_change_readiness_gate():
    source = Path("app/microstructure_research.py").read_text()
    assert "MICROSTRUCTURE_RECORDER_OWNER" in source
    assert '"external"' in source
    assert "load_runtime_status" in source
    assert "microstructure-readiness-v1" in source
    assert "promotion_allowed" in source


def test_standalone_supervisor_retries_singleton_instead_of_exiting():
    source = Path("research/microstructure/standalone.py").read_text()
    assert "while not stop.is_set()" in source
    assert "LOCK_RETRY_SECONDS" in source
    assert "singleton_acquired" in source
    assert "MicrostructureRecorder(config)" in source


def test_external_smoke_requires_standalone_role_and_exact_sha():
    source = Path("scripts/production_microstructure_external_smoke.py").read_text()
    assert 'candidate.get("process_role") == "standalone"' in source
    assert 'candidate.get("source_commit_sha") == expected_sha' in source
    assert 'candidate.get("external_service_healthy") is True' in source


# Connector-authored CI trigger; no behavioral effect.
