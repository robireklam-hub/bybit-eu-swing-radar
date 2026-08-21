import asyncio
from types import SimpleNamespace

import prospective_funnel_worker as worker


def test_strict_setups_from_scan_uses_only_authoritative_strict_lists():
    scan = {
        "strict_longs": [{"symbol": "BTCUSDC", "side": "long"}],
        "strict_shorts": [{"symbol": "ETHUSDC", "side": "short"}],
        "watch_only_longs": [{"symbol": "SOLUSDC", "side": "long"}],
    }
    assert worker._strict_setups_from_scan(scan) == [
        {"symbol": "BTCUSDC", "side": "long"},
        {"symbol": "ETHUSDC", "side": "short"},
    ]


def test_decode_cache_payload_rejects_non_object_json():
    assert worker._decode_cache_payload('[1,2,3]') == {}
    assert worker._decode_cache_payload('{"data_as_of":"x"}') == {"data_as_of": "x"}


def test_pending_barrier_symbols_are_forced_into_deep_universe_without_duplicates():
    def fast(symbol):
        return SimpleNamespace(instrument=SimpleNamespace(symbol=symbol))

    btc = fast("BTCUSDC")
    sol = fast("SOLUSDC")
    xrp = fast("XRPUSDC")
    output = worker._force_required_deep_symbols(
        [btc],
        [btc, sol, xrp],
        ["SOLUSDC", "BTCUSDC", "MISSINGUSDC"],
    )
    assert [item.instrument.symbol for item in output] == ["BTCUSDC", "SOLUSDC"]


def test_persist_standalone_capture_writes_only_dedicated_research_status(monkeypatch):
    calls = {"cache": []}

    class Tx:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Connection:
        def transaction(self):
            return Tx()
        async def close(self):
            calls["closed"] = True

    connection = Connection()

    async def fake_connect(*args, **kwargs):
        return connection

    async def fake_live_setups(conn):
        assert conn is connection
        return ([{"symbol": "BTCUSDC", "side": "long"}], "2026-08-18T15:30:00+00:00")

    async def fake_persist(conn, analyses, **kwargs):
        assert conn is connection
        assert analyses == ["analysis"]
        assert kwargs["live_setups"] == [{"symbol": "BTCUSDC", "side": "long"}]
        return {
            "status": "COMPLETE",
            "research_only": True,
            "label_free": True,
            "outcome_labels_stored": False,
            "current_run": {"observed_snapshots": 2, "inserted_snapshots": 2},
            "cumulative": {"distinct_sweep_events": 1},
        }

    async def fake_barrier(conn, analyses, **kwargs):
        assert conn is connection
        assert analyses == ["analysis"]
        assert kwargs["required_symbols"] == ["SOLUSDC"]
        return {
            "status": "COMPLETE",
            "research_only": True,
            "parent_strategy_version": "0.7.5",
            "current_run": {"inserted_new_parents": 0},
            "cumulative": {"parent_events": 0},
        }

    async def fake_upsert(conn, key, payload):
        assert conn is connection
        calls["cache"].append((key, payload))

    monkeypatch.setattr(worker.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(worker, "_load_authoritative_live_setups", fake_live_setups)
    monkeypatch.setattr(worker, "persist_v073_prospective_funnel", fake_persist)
    monkeypatch.setattr(worker, "persist_day_barrier_clear_rearm", fake_barrier)
    monkeypatch.setattr(worker.live, "upsert_cache", fake_upsert)

    result = asyncio.run(
        worker.persist_standalone_capture(
            ["analysis"],
            {"deep_analyzed_pairs": 1},
            captured_at=worker.datetime(2026, 8, 18, 15, 32, tzinfo=worker.timezone.utc),
            required_barrier_symbols=["SOLUSDC"],
        )
    )

    assert result["execution_mode"] == "STANDALONE_RAILWAY_CRON"
    assert result["live_worker_inline_recorder"] is False
    assert result["live_worker_mutation"] is False
    assert result["authoritative_live_strict_setups"] == 1
    assert result["barrier_clear_rearm"]["parent_strategy_version"] == "0.7.5"
    assert calls["closed"] is True
    assert [key for key, _ in calls["cache"]] == [worker.STATUS_CACHE_KEY]
    assert worker.STATUS_CACHE_KEY == "day_trade_prospective_funnel_status"
    assert worker.STATUS_CACHE_KEY not in {"day_trade_scan", "day_trade_status"}
