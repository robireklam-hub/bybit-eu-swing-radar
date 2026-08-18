import asyncio

from journal import STRATEGY_VERSION, persist_day_journal


class FakeConnection:
    def __init__(self):
        self.fetch_calls = []
        self.fetchval_calls = []
        self.execute_calls = []

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        return "OK"

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return []

    async def fetchval(self, sql, *args):
        self.fetchval_calls.append((sql, args))
        return 0


def test_persist_day_journal_isolates_open_evaluation_and_active_count_by_strategy_version():
    connection = FakeConnection()
    result = asyncio.run(
        persist_day_journal(
            connection,
            candidates=[],
            bars_by_symbol={},
            scan={"data_quality": "GOOD", "coverage": {}},
            status={"worker": {}},
        )
    )

    open_queries = [
        call
        for call in connection.fetch_calls
        if "FROM day_trade_signal_journal" in call[0]
    ]
    assert len(open_queries) == 1
    open_sql, open_args = open_queries[0]
    assert "status = 'OPEN' AND strategy_version = $1" in open_sql
    assert open_args == (STRATEGY_VERSION,)

    active_queries = [
        call
        for call in connection.fetchval_calls
        if "FROM day_trade_signal_journal" in call[0]
    ]
    assert len(active_queries) == 1
    active_sql, active_args = active_queries[0]
    assert "status = 'OPEN' AND strategy_version = $1" in active_sql
    assert active_args == (STRATEGY_VERSION,)

    assert result["strategy_version"] == STRATEGY_VERSION
    assert result["active_signals"] == 0
