import asyncio

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


def test_required_barrier_symbols_are_forced_into_deep_universe():
    class Instrument:
        def __init__(self, symbol):
            self.symbol = symbol

    class Fast:
        def __init__(self, symbol):
            self.instrument = Instrument(symbol)

    btc = Fast("BTCUSDC")
    eth = Fast("ETHUSDC")
    sol = Fast("SOLUSDC")
    result = worker._force_required_deep_symbols([btc], [btc, eth, sol], ["SOLUSDC", "solusdc"])
    assert [item.instrument.symbol for item in result] == ["BTCUSDC", "SOLUSDC"]


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

    async def fake_funnel(conn, analyses, **kwargs):
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

    async def fake_parent(conn, candidates, **kwargs):
        assert conn is connection
        assert candidates == [{"symbol": "BTCUSDC", "strategy_version": "0.7.5"}]
        return {
            "status": "COMPLETE",
            "research_only": True,
            "label_free": True,
            "execution_authorized": False,
            "live_strategy_mutation": False,
            "parent_strategy_version": "0.7.5",
            "source_commit_sha": "sha",
            "prospective_start_at": "2026-08-18T15:32:00+00:00",
            "captured_at": "2026-08-18T15:32:00+00:00",
            "admitted_this_run": 0,
            "inserted_this_run": 0,
            "total_frozen_parents": 0,
            "outcome_visibility": "LOCKED_UNTIL_PREREGISTERED_DEVELOPMENT_GATE",
        }

    async def fake_observer(conn, analyses, **kwargs):
        assert conn is connection
        assert analyses == ["analysis"]
        return {
            "status": "COMPLETE",
            "research_only": True,
            "label_free": True,
            "execution_authorized": False,
            "live_strategy_mutation": False,
            "scoring_mutation": False,
            "eligibility_mutation": False,
            "execution_mutation": False,
            "parent_strategy_version": "0.7.5",
            "source_commit_sha": "sha",
            "captured_at": "2026-08-18T15:32:00+00:00",
            "resolved_this_run": {},
            "pending_without_analysis_this_run": 0,
            "cumulative": {"pending": 0, "cleared": 0, "invalidated_boundary": 0, "invalidated_structure": 0},
            "outcome_visibility": "LOCKED_UNTIL_PREREGISTERED_DEVELOPMENT_GATE",
        }

    async def fake_partition(conn):
        assert conn is connection
        return {
            "study": "day-barrier-clear-rearm-v1",
            "partition_spec_version": "day-barrier-clear-partition-v1",
            "research_only": True,
            "label_blind_partition": True,
            "outcome_fields_used": False,
            "development_target": 60,
            "validation_target": 40,
            "terminal_event_count": 0,
            "development_partition_ready": False,
            "development_analysis_eligible": False,
            "development_event_ids": [],
            "development_partition_fingerprint": None,
            "validation_partition_ready": False,
            "validation_event_ids": [],
            "validation_partition_fingerprint": None,
            "outcome_visible": False,
            "threshold_search_allowed": False,
            "promotion_allowed": False,
            "live_strategy_mutation": False,
            "execution_authorized": False,
        }

    async def fake_upsert(conn, key, payload):
        assert conn is connection
        calls["cache"].append((key, payload))

    monkeypatch.setattr(worker.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(worker, "_load_authoritative_live_setups", fake_live_setups)
    monkeypatch.setattr(worker, "_build_v075_barrier_candidates", lambda analyses, captured_at: [
        {"symbol": "BTCUSDC", "strategy_version": "0.7.5"}
    ])
    monkeypatch.setattr(worker, "persist_v073_prospective_funnel", fake_funnel)
    monkeypatch.setattr(worker, "persist_parent_batch", fake_parent)
    monkeypatch.setattr(worker, "persist_pending_resolutions", fake_observer)
    monkeypatch.setattr(worker, "load_partition_status", fake_partition)
    monkeypatch.setattr(worker.live, "upsert_cache", fake_upsert)

    result = asyncio.run(
        worker.persist_standalone_capture(
            ["analysis"],
            {"deep_analyzed_pairs": 1},
            captured_at=worker.datetime(2026, 8, 18, 15, 32, tzinfo=worker.timezone.utc),
            required_barrier_symbols=["BTCUSDC"],
        )
    )

    assert result["execution_mode"] == "STANDALONE_RAILWAY_CRON"
    assert result["live_worker_inline_recorder"] is False
    assert result["live_worker_mutation"] is False
    assert result["authoritative_live_strict_setups"] == 1
    assert result["barrier_clear_rearm"]["parent_strategy_version"] == "0.7.5"
    assert result["barrier_clear_rearm"]["observer"]["execution_authorized"] is False
    assert result["barrier_clear_rearm"]["observer"]["partition"]["terminal_event_count"] == 0
    assert result["barrier_clear_rearm"]["observer"]["partition"]["outcome_visible"] is False
    assert calls["closed"] is True
    keys = [key for key, _ in calls["cache"]]
    assert keys == [
        worker.STATUS_CACHE_KEY,
        worker.BARRIER_PARENT_STATUS_CACHE_KEY,
        worker.BARRIER_OBSERVER_STATUS_CACHE_KEY,
    ]
    assert set(keys).isdisjoint({"day_trade_scan", "day_trade_status"})
