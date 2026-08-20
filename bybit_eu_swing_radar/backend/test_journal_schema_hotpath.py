import pytest

import journal
import journal_core


class _Tx:
    def __init__(self, events):
        self.events = events

    async def __aenter__(self):
        self.events.append("tx-enter")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.events.append("tx-exit")
        return False


class _Connection:
    def __init__(self, schema_complete: bool):
        self.schema_complete = schema_complete
        self.events = []
        self.executed = []

    async def fetchval(self, query, *args):
        self.events.append("fetchval")
        assert "to_regclass('public.day_trade_signal_journal')" in query
        assert "to_regclass('public.day_trade_journal_runs')" in query
        assert "to_regclass('public.idx_day_journal_runs_run_at')" in query
        return self.schema_complete

    def transaction(self):
        return _Tx(self.events)

    async def execute(self, sql, *args):
        self.events.append("execute")
        self.executed.append(sql)
        return "OK"


@pytest.mark.asyncio
async def test_existing_journal_schema_skips_all_ddl():
    connection = _Connection(schema_complete=True)

    await journal.ensure_journal_schema(connection)

    assert connection.events == ["fetchval"]
    assert connection.executed == []


@pytest.mark.asyncio
async def test_missing_journal_relation_runs_bounded_schema_install():
    connection = _Connection(schema_complete=False)

    await journal.ensure_journal_schema(connection)

    assert connection.events == [
        "fetchval",
        "tx-enter",
        "execute",
        "execute",
        "execute",
        "tx-exit",
    ]
    assert connection.executed[0] == "SET LOCAL lock_timeout = '5s'"
    assert connection.executed[1] == "SET LOCAL statement_timeout = '10s'"
    assert connection.executed[2] == journal_core.SCHEMA_SQL


def test_facade_preserves_existing_journal_writer_implementation():
    assert journal.persist_day_journal is journal_core.persist_day_journal
    assert journal.STRATEGY_VERSION == journal_core.STRATEGY_VERSION == "0.7.5"
