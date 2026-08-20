import asyncio
from datetime import timedelta

import pytest

import research.microstructure.alignment as v1
import research.microstructure.alignment_v2 as v2
import research.microstructure.alignment_v3 as v3


class FakeConnection:
    def __init__(self):
        self.fetch_calls = []
        self.closed = False

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return []

    async def close(self):
        self.closed = True


def _row(strategy_version: str = "0.7.5") -> dict:
    opened_at = v3.COHORT_START_AT + timedelta(minutes=5)
    return {
        "signal_id": 1,
        "signal_key": "v075-forward-1",
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


def test_v3_preregistration_is_v075_isolated_forward_and_frozen():
    spec = v3.alignment_spec()

    assert v1.PREREGISTERED_STRATEGY_VERSION == "0.7.3"
    assert v2.PREREGISTERED_STRATEGY_VERSION == "0.7.4"
    assert v3.PREREGISTERED_STRATEGY_VERSION == "0.7.5"
    assert spec["spec_version"] == "microstructure-forward-alignment-v3"
    assert spec["parent_spec_version"] == v2.SPEC_VERSION
    assert spec["feature_definition_source"] == v1.SPEC_VERSION
    assert spec["strategy_version_isolated"] is True
    assert spec["forward_only"] is True
    assert spec["label_blind"] is True
    assert spec["post_signal_data_used"] is False
    assert spec["outcome_visible"] is False
    assert spec["promotion_allowed"] is False
    assert spec["threshold_search_allowed"] is False
    assert spec["feature_definitions_frozen_from_parent"] is True
    assert tuple(spec["windows_seconds"]) == v1.WINDOW_SECONDS
    assert spec["minimum_signal_sample"] == {"total": 60, "per_symbol": 10}
    assert spec["hypotheses"] == list(v1.HYPOTHESES)
    assert v3.COHORT_START_AT > v3.PRODUCTION_VERIFIED_BY
    evidence = spec["production_activation_evidence"]
    assert evidence["strategy_merge_sha"] == "6230162eaa2bc5b78a630cd2bb23a0929a65dd4f"
    assert evidence["exact_production_verifier_pr"] == 275
    assert evidence["cohort_start_rule"] == "strictly_after_exact_production_verification"
    assert "j.strategy_version = $4" in v3.ALIGNMENT_SQL
    lowered_sql = v3.ALIGNMENT_SQL.lower()
    for forbidden in ("net_r", "gross_r", "exit_reason", "closed_at"):
        assert forbidden not in lowered_sql


def test_v3_loader_clamps_to_forward_start_and_filters_v075(monkeypatch):
    connection = FakeConnection()

    async def fake_connect(*args, **kwargs):
        return connection

    monkeypatch.setattr(v3.asyncpg, "connect", fake_connect)
    since = v3.COHORT_START_AT - timedelta(days=1)
    until = v3.COHORT_START_AT + timedelta(days=1)

    result = asyncio.run(
        v3.load_feature_rows(
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
        v3.COHORT_START_AT,
        until,
        "0.7.5",
    )


def test_v3_precohort_window_returns_empty_without_database_access(monkeypatch):
    async def fail_connect(*args, **kwargs):
        pytest.fail("database must not be accessed for a fully pre-cohort window")

    monkeypatch.setattr(v3.asyncpg, "connect", fail_connect)
    result = asyncio.run(
        v3.load_feature_rows(
            "postgres://unused",
            ("BTCUSDC",),
            v3.COHORT_START_AT - timedelta(days=2),
            v3.COHORT_START_AT - timedelta(seconds=1),
        )
    )
    assert result == []


def test_v3_feature_rows_keep_actual_v075_version_and_v3_spec():
    feature = v3.build_feature_rows([_row()])[0]
    assert feature["strategy_version"] == "0.7.5"
    assert feature["spec_version"] == v3.SPEC_VERSION


@pytest.mark.parametrize("contaminating_version", ["0.7.3", "0.7.4", "", "0.7.6"])
def test_v3_feature_builder_fails_closed_on_strategy_contamination(contaminating_version):
    with pytest.raises(ValueError, match="v3 alignment strategy contamination"):
        v3.build_feature_rows([_row(contaminating_version)])
