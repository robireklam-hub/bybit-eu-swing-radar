import asyncio
from datetime import timedelta

import pytest

import research.microstructure.alignment as v1
import research.microstructure.alignment_v2 as v2


class FakeConnection:
    def __init__(self):
        self.fetch_calls = []
        self.closed = False

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return []

    async def close(self):
        self.closed = True


def _row(strategy_version: str = "0.7.4") -> dict:
    opened_at = v2.COHORT_START_AT + timedelta(minutes=5)
    return {
        "signal_id": 1,
        "signal_key": "v074-forward-1",
        "strategy_version": strategy_version,
        "signal_class": "DAY",
        "symbol": "BTCUSDC",
        "side": "long",
        "opened_at": opened_at,
        "setup_type": "test",
        "bucket_start": opened_at - timedelta(seconds=5),
        "bucket_seconds": 5,
        "signed_quote_flow": 100.0,
        "total_quote_volume": 1000.0,
        "spread_bps": 2.0,
        "mid": 100.0,
        "microprice": 100.01,
        "imbalance_10": 0.2,
        "imbalance_50": 0.1,
        "bid_added_quote": 50.0,
        "bid_removed_quote": 10.0,
        "ask_added_quote": 10.0,
        "ask_removed_quote": 50.0,
        "book_ready": True,
        "book_message_count": 4,
    }


def test_v2_preregistration_is_strategy_isolated_forward_and_frozen():
    spec = v2.alignment_spec()

    assert v1.PREREGISTERED_STRATEGY_VERSION == "0.7.3"
    assert v2.PREREGISTERED_STRATEGY_VERSION == "0.7.4"
    assert spec["spec_version"] == "microstructure-forward-alignment-v2"
    assert spec["parent_spec_version"] == v1.SPEC_VERSION
    assert spec["preregistered_strategy_version"] == "0.7.4"
    assert spec["strategy_version_isolated"] is True
    assert spec["forward_only"] is True
    assert spec["label_blind"] is True
    assert spec["post_signal_data_used"] is False
    assert spec["outcome_visible"] is False
    assert spec["promotion_allowed"] is False
    assert spec["feature_definitions_frozen_from_parent"] is True
    assert tuple(spec["windows_seconds"]) == v1.WINDOW_SECONDS
    assert spec["minimum_signal_sample"] == {"total": 60, "per_symbol": 10}
    assert spec["hypotheses"] == list(v1.HYPOTHESES)
    assert "j.strategy_version = $4" in v2.ALIGNMENT_SQL
    lowered_sql = v2.ALIGNMENT_SQL.lower()
    for forbidden in ("net_r", "gross_r", "exit_reason", "closed_at"):
        assert forbidden not in lowered_sql


def test_v2_loader_clamps_to_forward_start_and_filters_v074(monkeypatch):
    connection = FakeConnection()

    async def fake_connect(*args, **kwargs):
        return connection

    monkeypatch.setattr(v2.asyncpg, "connect", fake_connect)
    since = v2.COHORT_START_AT - timedelta(days=1)
    until = v2.COHORT_START_AT + timedelta(days=1)

    result = asyncio.run(
        v2.load_feature_rows(
            "postgres://unused",
            ("BTCUSDC", "ETHUSDC", "SOLUSDC"),
            since,
            until,
        )
    )

    assert result == []
    assert connection.closed is True
    assert len(connection.fetch_calls) == 1
    sql, args = connection.fetch_calls[0]
    assert "j.strategy_version = $4" in sql
    assert args == (
        ["BTCUSDC", "ETHUSDC", "SOLUSDC"],
        v2.COHORT_START_AT,
        until,
        "0.7.4",
    )


def test_v2_precohort_window_returns_empty_without_database_access(monkeypatch):
    async def fail_connect(*args, **kwargs):
        pytest.fail("database must not be accessed for a fully pre-cohort window")

    monkeypatch.setattr(v2.asyncpg, "connect", fail_connect)
    result = asyncio.run(
        v2.load_feature_rows(
            "postgres://unused",
            ("BTCUSDC",),
            v2.COHORT_START_AT - timedelta(days=2),
            v2.COHORT_START_AT - timedelta(seconds=1),
        )
    )
    assert result == []


def test_v2_feature_rows_keep_actual_v074_version_and_v2_spec():
    feature = v2.build_feature_rows([_row()])[0]
    assert feature["strategy_version"] == "0.7.4"
    assert feature["spec_version"] == v2.SPEC_VERSION


def test_v2_feature_builder_fails_closed_on_strategy_contamination():
    with pytest.raises(ValueError, match="strategy contamination"):
        v2.build_feature_rows([_row("0.7.3")])
