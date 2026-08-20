import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from research.microstructure.controlled_pullback_store_v2 import (
    CREATE_TABLE_SQL,
    INSERT_SQL,
    build_storage_rows,
    persist_detection_result,
    store_contract,
)


START = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _result():
    momentum_end = START + timedelta(seconds=120)
    momentum_trigger = momentum_end + timedelta(seconds=5)
    event_trigger = START + timedelta(seconds=135)
    return {
        "detector": {
            "detector_id": "microstructure-controlled-pullback-detector-v2",
            "experiment_id": "microstructure-controlled-pullback-reacceleration-v2",
            "strategy_version": "0.7.5",
            "research_only": True,
            "label_blind": True,
            "outcome_fields_read": False,
            "promotion_allowed": False,
            "live_strategy_mutation": False,
        },
        "forward_start_utc": (START + timedelta(seconds=100)).isoformat(),
        "momentum_candidates": [
            {
                "symbol": "BTCUSDC",
                "direction": "long",
                "momentum_start_at": (START + timedelta(seconds=60)).isoformat(),
                "momentum_end_at": momentum_end.isoformat(),
                "momentum_trigger_at": momentum_trigger.isoformat(),
                "momentum_return_60s": 0.01,
                "momentum_flow_share": 0.30,
                "comparator_class": "MOMENTUM_ONLY_SAME_DIRECTION_SAME_SYMBOL",
                "research_only": True,
                "label_blind": True,
                "outcome_visible": False,
            }
        ],
        "controlled_pullback_events": [
            {
                "event_key": "microstructure-controlled-pullback-reacceleration-v2:BTCUSDC:long:1",
                "experiment_id": "microstructure-controlled-pullback-reacceleration-v2",
                "strategy_version": "0.7.5",
                "detector_id": "microstructure-controlled-pullback-detector-v2",
                "symbol": "BTCUSDC",
                "direction": "long",
                "momentum_start_at": (START + timedelta(seconds=60)).isoformat(),
                "momentum_end_at": momentum_end.isoformat(),
                "pullback_at": (START + timedelta(seconds=125)).isoformat(),
                "trigger_at": event_trigger.isoformat(),
                "pullback_retracement_fraction": 0.30,
                "reacceleration_flow_share": 0.25,
                "reacceleration_book_pressure": 0.60,
                "research_only": True,
                "label_blind": True,
                "outcome_visible": False,
                "promotion_allowed": False,
                "live_strategy_mutation": False,
            }
        ],
        "outcome_visible": False,
        "promotion_allowed": False,
        "live_strategy_mutation": False,
    }


def test_store_contract_is_immutable_label_blind_and_has_no_outcome_schema():
    contract = store_contract()
    assert contract["strategy_version"] == "0.7.5"
    assert contract["research_only"] is True
    assert contract["label_blind"] is True
    assert contract["outcome_columns_present"] is False
    assert contract["outcome_visible"] is False
    assert contract["promotion_allowed"] is False
    assert contract["conflict_policy"] == "DO_NOTHING_IMMUTABLE_FIRST_SEEN"
    sql = CREATE_TABLE_SQL.lower()
    for forbidden_column in ("net_r ", "gross_r ", "pnl ", "future_return ", "mae_15m ", "mfe_15m "):
        assert forbidden_column not in sql
    assert "on conflict (record_key) do nothing" in INSERT_SQL.lower()
    assert "do update" not in INSERT_SQL.lower()


def test_storage_rows_preserve_closed_bucket_timestamp_semantics():
    rows = build_storage_rows(_result())
    assert len(rows) == 2
    momentum, event = rows
    assert momentum["record_class"] == "MOMENTUM_ONLY"
    assert event["record_class"] == "CONTROLLED_PULLBACK"
    expected_momentum_trigger = (START + timedelta(seconds=125)).isoformat()
    assert momentum["momentum_trigger_at"] == expected_momentum_trigger
    assert event["momentum_trigger_at"] == expected_momentum_trigger
    assert event["trigger_at"] == (START + timedelta(seconds=135)).isoformat()
    assert event["pullback_at"] == (START + timedelta(seconds=125)).isoformat()
    assert momentum["record_key"] != event["record_key"]
    assert all(row["strategy_version"] == "0.7.5" for row in rows)


def test_store_rejects_recursive_outcome_contamination_and_open_gates():
    payload = _result()
    payload["controlled_pullback_events"][0]["nested"] = {"net_r": 1.2}
    with pytest.raises(ValueError, match="forbidden outcome fields"):
        build_storage_rows(payload)

    payload = _result()
    payload["outcome_visible"] = True
    with pytest.raises(ValueError, match="opened outcome visibility"):
        build_storage_rows(payload)


def test_store_rejects_pre_forward_start_and_wrong_detector_identity():
    payload = _result()
    payload["forward_start_utc"] = (START + timedelta(seconds=140)).isoformat()
    with pytest.raises(ValueError, match="pre-forward-start record"):
        build_storage_rows(payload)

    payload = _result()
    payload["detector"]["strategy_version"] = "0.7.4"
    with pytest.raises(ValueError, match="detector invariant failed"):
        build_storage_rows(payload)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Connection:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.insert_count = 0

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        if sql == INSERT_SQL:
            self.insert_count += 1
            return "INSERT 0 1" if self.insert_count == 1 else "INSERT 0 0"
        return "OK"

    def transaction(self):
        return _Transaction()

    async def close(self):
        self.closed = True


def test_persistence_counts_first_seen_and_duplicates_without_updates(monkeypatch):
    connection = _Connection()

    async def fake_connect(*args, **kwargs):
        return connection

    import research.microstructure.controlled_pullback_store_v2 as store

    monkeypatch.setattr(store.asyncpg, "connect", fake_connect)
    status = asyncio.run(persist_detection_result("postgres://unused", _result()))
    assert status["candidate_records"] == 2
    assert status["inserted_records"] == 1
    assert status["duplicate_records"] == 1
    assert status["outcome_visible"] is False
    assert status["promotion_allowed"] is False
    assert connection.closed is True
    assert connection.insert_count == 2
