import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest


def load_worker(monkeypatch):
    asyncpg = ModuleType("asyncpg")
    asyncpg.Connection = object
    asyncpg.connect = None
    httpx = ModuleType("httpx")
    httpx.AsyncClient = object
    httpx.Timeout = object
    httpx.Limits = object
    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg)
    monkeypatch.setitem(sys.modules, "httpx", httpx)
    sys.modules.pop("flow_worker", None)
    return importlib.import_module("flow_worker")


def test_disabled_returns_complete_metadata_without_database_access(monkeypatch):
    worker = load_worker(monkeypatch)
    calls = []
    worker.uuid4 = lambda: calls.append("uuid") or "batch-disabled"
    worker.FLOW_CONTEXT_ENABLED = False
    worker.asyncpg.connect = lambda *args, **kwargs: pytest.fail("database connection attempted")
    worker.upsert_cache = lambda *args, **kwargs: pytest.fail("cache write attempted")

    result = asyncio.run(worker.run_flow_worker())

    assert calls == ["uuid"]
    assert result == {
        "enabled": False,
        "status": "DISABLED",
        "flow_batch_id": "batch-disabled",
        "symbols": [],
        "processed": 0,
        "good": 0,
        "partial": 0,
        "no_derivative_match": 0,
        "errors": [],
    }


def test_missing_database_url_fails_fast_with_generated_batch_id(monkeypatch):
    worker = load_worker(monkeypatch)
    calls = []
    worker.uuid4 = lambda: calls.append("uuid") or "batch-no-db"
    worker.FLOW_CONTEXT_ENABLED = True
    worker.DATABASE_URL = ""
    worker.asyncpg.connect = lambda *args, **kwargs: pytest.fail("database connection attempted")

    with pytest.raises(RuntimeError, match=r"DATABASE_URL.*batch-no-db"):
        asyncio.run(worker.run_flow_worker())
    assert calls == ["uuid"]


class FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class FakeConnection:
    async def close(self):
        return None

    def transaction(self):
        return FakeClient()


def test_no_fresh_setups_reuses_single_batch_id(monkeypatch):
    worker = load_worker(monkeypatch)
    calls = []
    worker.uuid4 = lambda: calls.append("uuid") or "batch-empty"
    worker.FLOW_CONTEXT_ENABLED = True
    worker.DATABASE_URL = "postgresql://configured"
    worker.httpx.Timeout = lambda *args, **kwargs: None
    worker.httpx.Limits = lambda *args, **kwargs: None
    worker.httpx.AsyncClient = lambda *args, **kwargs: FakeClient()
    connection = FakeConnection()

    async def connect(*args, **kwargs):
        return connection

    async def no_setups(_connection):
        return []

    written = []

    async def capture(_connection, key, payload):
        written.append((key, payload))

    worker.asyncpg.connect = connect
    worker.load_fresh_setups = no_setups
    worker.upsert_cache = capture
    worker.BybitDerivativesAPI = lambda client: SimpleNamespace()

    result = asyncio.run(worker.run_flow_worker())

    assert calls == ["uuid"]
    assert result["flow_batch_id"] == "batch-empty"
    assert result["symbols"] == []
    assert written == [("day_trade_flow_status", result)]


def test_normal_worker_reuses_single_batch_id_for_payload_and_status(monkeypatch):
    worker = load_worker(monkeypatch)
    source = importlib.util.find_spec("flow_worker").loader.get_source("flow_worker")
    assert source.count("uuid4()") == 1
    calls = []
    worker.uuid4 = lambda: calls.append("uuid") or "batch-normal"
    worker.FLOW_CONTEXT_ENABLED = True
    worker.DATABASE_URL = "postgresql://configured"
    worker.httpx.Timeout = lambda *args, **kwargs: None
    worker.httpx.Limits = lambda *args, **kwargs: None
    worker.httpx.AsyncClient = lambda *args, **kwargs: FakeClient()
    connection = FakeConnection()

    async def connect(*args, **kwargs):
        return connection

    async def setups(_connection):
        return [{"symbol": "BTCUSDC", "base_asset": "BTC", "data_as_of": "2026-08-13T12:00:00+00:00"}]

    class FakeAPI:
        def __init__(self, client):
            pass

        async def linear_instruments(self):
            return []

        async def linear_tickers(self):
            return []

    def build(**kwargs):
        return {"coverage_status": "NO_BYBIT_GLOBAL_LINEAR_PERPETUAL_MATCH"}

    written = []

    async def capture(_connection, key, payload):
        written.append((key, dict(payload)))

    worker.asyncpg.connect = connect
    worker.load_fresh_setups = setups
    worker.BybitDerivativesAPI = FakeAPI
    worker.build_flow_payload = build
    worker.upsert_cache = capture

    result = asyncio.run(worker.run_flow_worker())

    assert calls == ["uuid"]
    assert result["flow_batch_id"] == "batch-normal"
    assert result["symbols"] == ["BTCUSDC"]
    assert written[0][0] == "day_trade_flow:BTCUSDC"
    assert written[0][1]["flow_batch_id"] == "batch-normal"
    assert written[1] == ("day_trade_flow_status", result)
