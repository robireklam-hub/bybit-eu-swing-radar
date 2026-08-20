from datetime import datetime, timedelta, timezone

import pytest

from research.microstructure.controlled_pullback_runtime_v2 import (
    LOOKBACK_SECONDS,
    runtime_contract,
    run_prospective_cycle,
)


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.fetch_calls = []
        self.execute_calls = []

    async def fetch(self, sql, symbols, start, end):
        self.fetch_calls.append((sql, symbols, start, end))
        return self.rows

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        if sql.lstrip().startswith("INSERT INTO research_controlled_pullback_v2_records"):
            return "INSERT 0 1"
        return "OK"


def _bucket(symbol, at, mid=100.0):
    return {
        "symbol": symbol,
        "bucket_start": at,
        "bucket_seconds": 5,
        "mid": mid,
        "spread_bps": 1.0,
        "bid_depth_5_quote": 100000.0,
        "ask_depth_5_quote": 100000.0,
        "signed_quote_flow": 0.0,
        "total_quote_volume": 100.0,
        "bid_added_quote": 10.0,
        "bid_removed_quote": 10.0,
        "ask_added_quote": 10.0,
        "ask_removed_quote": 10.0,
        "book_ready": True,
    }


def test_runtime_contract_is_research_only_and_label_blind():
    contract = runtime_contract()
    assert contract["research_only"] is True
    assert contract["label_blind"] is True
    assert contract["outcome_fields_read"] is False
    assert contract["outcome_visible"] is False
    assert contract["promotion_allowed"] is False
    assert contract["live_strategy_mutation"] is False
    assert contract["lookback_seconds"] == LOOKBACK_SECONDS


@pytest.mark.asyncio
async def test_cycle_uses_existing_connection_and_does_not_create_live_mutation():
    now = datetime(2026, 8, 20, 10, 30, tzinfo=timezone.utc)
    rows = []
    for symbol in ("BTCUSDC", "ETHUSDC", "SOLUSDC"):
        for index in range(60):
            rows.append(_bucket(symbol, now - timedelta(seconds=(60 - index) * 5)))
    connection = FakeConnection(rows)

    result = await run_prospective_cycle(connection, now=now)

    assert len(connection.fetch_calls) == 1
    _, symbols, start, end = connection.fetch_calls[0]
    assert symbols == ["BTCUSDC", "ETHUSDC", "SOLUSDC"]
    assert end == now
    assert start >= now - timedelta(seconds=LOOKBACK_SECONDS)
    assert result["bucket_rows"] == len(rows)
    assert result["outcome_visible"] is False
    assert result["promotion_allowed"] is False
    assert result["live_strategy_mutation"] is False
    assert result["candidate_records"] == 0


@pytest.mark.asyncio
async def test_cycle_rejects_outcome_contamination_before_persistence():
    now = datetime(2026, 8, 20, 10, 30, tzinfo=timezone.utc)
    contaminated = _bucket("BTCUSDC", now - timedelta(seconds=5))
    contaminated["future_return"] = 0.1
    connection = FakeConnection([contaminated])

    # LOAD_BUCKETS_SQL selects a fixed label-blind column set, so an upstream row
    # object cannot inject outcome fields into detector input.
    result = await run_prospective_cycle(connection, now=now)
    assert result["candidate_records"] == 0
    assert all("future_return" not in str(call) for call in connection.execute_calls)
