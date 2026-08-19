import asyncio
from datetime import datetime, timezone

import app.microstructure_research as research_api
import research.microstructure.alignment as alignment


class FakeConnection:
    def __init__(self):
        self.fetch_calls = []
        self.closed = False

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return []

    async def close(self):
        self.closed = True


async def _fake_connect_factory(connection, *args, **kwargs):
    return connection


def test_alignment_spec_pins_preregistered_v073_strategy_version():
    spec = alignment.alignment_spec()
    assert alignment.PREREGISTERED_STRATEGY_VERSION == "0.7.3"
    assert spec["preregistered_strategy_version"] == "0.7.3"
    assert spec["strategy_version_isolated"] is True
    assert "j.strategy_version = $4" in alignment.ALIGNMENT_SQL


def test_feature_loader_filters_journal_rows_to_preregistered_strategy(monkeypatch):
    connection = FakeConnection()

    async def fake_connect(*args, **kwargs):
        return connection

    monkeypatch.setattr(alignment.asyncpg, "connect", fake_connect)
    since = datetime(2026, 8, 16, tzinfo=timezone.utc)
    until = datetime(2026, 8, 19, tzinfo=timezone.utc)
    result = asyncio.run(
        alignment.load_feature_rows(
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
    assert args == (["BTCUSDC", "ETHUSDC", "SOLUSDC"], since, until, "0.7.3")


def test_alignment_coverage_counts_filter_same_preregistered_strategy(monkeypatch):
    connection = FakeConnection()

    async def fake_connect(*args, **kwargs):
        return connection

    monkeypatch.setattr(research_api.asyncpg, "connect", fake_connect)
    since = datetime(2026, 8, 16, tzinfo=timezone.utc)
    until = datetime(2026, 8, 19, tzinfo=timezone.utc)
    result = asyncio.run(
        research_api._load_journal_signal_counts(
            "postgres://unused",
            ("BTCUSDC", "ETHUSDC", "SOLUSDC"),
            since,
            until,
        )
    )
    assert result == {"BTCUSDC": 0, "ETHUSDC": 0, "SOLUSDC": 0}
    assert connection.closed is True
    assert len(connection.fetch_calls) == 1
    sql, args = connection.fetch_calls[0]
    assert "strategy_version = $4" in sql
    assert args == (["BTCUSDC", "ETHUSDC", "SOLUSDC"], since, until, "0.7.3")


def test_v074_signals_cannot_satisfy_v1_alignment_by_query_contract():
    assert alignment.PREREGISTERED_STRATEGY_VERSION != "0.7.4"
    assert "strategy_version = $4" in alignment.ALIGNMENT_SQL
