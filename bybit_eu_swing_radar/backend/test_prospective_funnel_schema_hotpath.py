import asyncio

from research import prospective_funnel_v073 as funnel


class Tx:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        return False


class Connection:
    def __init__(self, complete):
        self.complete = complete
        self.executed = []
    async def fetchval(self, query, *args):
        if "to_regclass" in query:
            return self.complete
        return None
    async def execute(self, query, *args):
        self.executed.append(query)
    def transaction(self):
        return Tx()


def test_complete_schema_skips_ddl():
    connection = Connection(True)
    asyncio.run(funnel._ensure_prospective_schema(connection))
    assert connection.executed == []


def test_missing_schema_uses_bounded_ddl_install():
    connection = Connection(False)
    asyncio.run(funnel._ensure_prospective_schema(connection))
    joined = "\n".join(connection.executed)
    assert "SET LOCAL lock_timeout = '5s'" in joined
    assert "SET LOCAL statement_timeout = '10s'" in joined
    assert funnel.PROSPECTIVE_SCHEMA_SQL in connection.executed
