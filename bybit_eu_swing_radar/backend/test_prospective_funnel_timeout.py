"""Regression coverage for bounded research-sidecar execution."""

import asyncio

import pytest

import day_worker


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Connection:
    def transaction(self):
        return _Tx()

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_prospective_funnel_timeout_degrades_without_blocking_live_persistence(monkeypatch):
    connection = _Connection()
    cache_writes = []

    async def fake_connect(*args, **kwargs):
        return connection

    async def fake_journal(*args, **kwargs):
        return {"new_signals": 0, "active_signals": 0}

    async def hanging_funnel(*args, **kwargs):
        await asyncio.sleep(1)
        return {"status": "COMPLETE"}

    async def fake_upsert(_connection, key, payload):
        cache_writes.append((key, payload))

    monkeypatch.setattr(day_worker.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(day_worker, "persist_day_journal", fake_journal)
    monkeypatch.setattr(day_worker, "persist_v073_prospective_funnel", hanging_funnel)
    monkeypatch.setattr(day_worker, "upsert_cache", fake_upsert)
    monkeypatch.setattr(day_worker, "DAY_PROSPECTIVE_FUNNEL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(day_worker, "DAY_PROSPECTIVE_FUNNEL_INLINE_ENABLED", True)

    scan = {
        "data_as_of": "2026-08-18T14:45:00+00:00",
        "data_as_of_budapest": "2026-08-18T16:45:00+02:00",
    }
    status = {}

    journal = await day_worker.persist_day_results(scan, [], status, {}, [])

    assert journal == {"new_signals": 0, "active_signals": 0}
    funnel = status["prospective_funnel"]
    assert funnel["status"] == "DEGRADED"
    assert funnel["research_only"] is True
    assert funnel["label_free"] is True
    assert funnel["outcome_labels_stored"] is False
    assert funnel["reason"] == "PROSPECTIVE_FUNNEL_TIMEOUT_AFTER_0.01S"
    assert scan["prospective_funnel"] == funnel
    assert {key for key, _ in cache_writes} >= {"day_trade_scan", "day_trade_status"}


def test_prospective_funnel_timeout_is_bounded_by_configuration():
    assert 1.0 <= day_worker.DAY_PROSPECTIVE_FUNNEL_TIMEOUT_SECONDS <= 30.0


@pytest.mark.asyncio
async def test_inline_recorder_disabled_skips_research_and_persists_live_caches(monkeypatch):
    connection = _Connection()
    cache_writes = []

    async def fake_connect(*args, **kwargs):
        return connection

    async def fake_journal(*args, **kwargs):
        return {"new_signals": 0, "active_signals": 0}

    async def must_not_run(*args, **kwargs):
        raise AssertionError("inline prospective recorder must not run when disabled")

    async def fake_upsert(_connection, key, payload):
        cache_writes.append((key, payload))

    monkeypatch.setattr(day_worker.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(day_worker, "persist_day_journal", fake_journal)
    monkeypatch.setattr(day_worker, "persist_v073_prospective_funnel", must_not_run)
    monkeypatch.setattr(day_worker, "upsert_cache", fake_upsert)
    monkeypatch.setattr(day_worker, "DAY_PROSPECTIVE_FUNNEL_INLINE_ENABLED", False)

    scan = {
        "data_as_of": "2026-08-18T15:00:00+00:00",
        "data_as_of_budapest": "2026-08-18T17:00:00+02:00",
    }
    status = {}

    journal = await day_worker.persist_day_results(scan, [], status, {}, [])

    assert journal == {"new_signals": 0, "active_signals": 0}
    funnel = status["prospective_funnel"]
    assert funnel["status"] == "DISABLED"
    assert funnel["enabled"] is False
    assert funnel["reason"] == "INLINE_RECORDER_DISABLED_FOR_LIVE_WORKER_ISOLATION"
    assert scan["prospective_funnel"] == funnel
    assert {key for key, _ in cache_writes} >= {"day_trade_scan", "day_trade_status"}
