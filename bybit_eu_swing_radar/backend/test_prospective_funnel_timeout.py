"""Regression coverage for hard separation of prospective research from live persistence."""

import pytest

import day_worker


class _Tx:
    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc, tb): return False

class _Connection:
    def transaction(self): return _Tx()
    async def close(self): return None

@pytest.mark.asyncio
async def test_live_persistence_has_no_inline_prospective_recorder(monkeypatch):
    connection=_Connection(); cache_writes=[]
    async def fake_connect(*args, **kwargs): return connection
    async def fake_journal(*args, **kwargs): return {"new_signals":0,"active_signals":0}
    async def fake_upsert(_connection,key,payload): cache_writes.append((key,payload))
    monkeypatch.setattr(day_worker.asyncpg,"connect",fake_connect)
    monkeypatch.setattr(day_worker,"persist_day_journal",fake_journal)
    monkeypatch.setattr(day_worker,"upsert_cache",fake_upsert)
    scan={"data_as_of":"2026-08-18T15:30:00+00:00","data_as_of_budapest":"2026-08-18T17:30:00+02:00"}
    status={}
    journal=await day_worker.persist_day_results(scan,[],status,{})
    assert journal=={"new_signals":0,"active_signals":0}
    funnel=status["prospective_funnel"]
    assert funnel["status"]=="EXTERNALIZED"
    assert funnel["enabled"] is False
    assert funnel["reason"]=="STANDALONE_RECORDER_OWNS_CAPTURE"
    assert funnel["execution_mode"]=="STANDALONE_RAILWAY_CRON"
    assert scan["prospective_funnel"]==funnel
    assert {key for key,_ in cache_writes} >= {"day_trade_scan","day_trade_status"}

def test_live_worker_exports_no_inline_recorder_controls():
    assert not hasattr(day_worker,"persist_v073_prospective_funnel")
    assert not hasattr(day_worker,"DAY_PROSPECTIVE_FUNNEL_INLINE_ENABLED")
    assert not hasattr(day_worker,"DAY_PROSPECTIVE_FUNNEL_TIMEOUT_SECONDS")
