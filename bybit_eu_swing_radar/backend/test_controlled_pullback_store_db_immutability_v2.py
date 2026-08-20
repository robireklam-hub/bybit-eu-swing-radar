import asyncio

from research.microstructure.controlled_pullback_store_v2 import (
    CREATE_INDEX_SQL,
    CREATE_TABLE_SQL,
    IMMUTABILITY_GUARD_SQL,
    install_schema,
    store_contract,
)


class _Connection:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "OK"


def test_controlled_pullback_v2_has_catalog_guarded_append_only_schema():
    schema = IMMUTABILITY_GUARD_SQL
    table = "research_controlled_pullback_v2_records"
    function = "reject_controlled_pullback_v2_mutation"

    assert "to_regprocedure(current_schema()" in schema
    assert function in schema
    assert "SELECT 1 FROM pg_trigger" in schema
    assert f"BEFORE UPDATE OR DELETE ON {table}" in schema
    assert f"BEFORE TRUNCATE ON {table}" in schema
    assert "FOR EACH ROW EXECUTE FUNCTION" in schema
    assert "FOR EACH STATEMENT EXECUTE FUNCTION" in schema
    assert "DROP TRIGGER" not in schema
    assert "CREATE OR REPLACE FUNCTION" not in schema

    contract = store_contract()
    assert contract["database_mutation_guard"] == "REJECT_UPDATE_DELETE_TRUNCATE"
    assert contract["conflict_policy"] == "DO_NOTHING_IMMUTABLE_FIRST_SEEN"
    assert contract["outcome_visible"] is False
    assert contract["promotion_allowed"] is False
    assert contract["live_strategy_mutation"] is False


def test_schema_installer_always_installs_db_mutation_guard_after_table_and_index():
    connection = _Connection()
    asyncio.run(install_schema(connection))
    assert [call[0] for call in connection.calls] == [
        CREATE_TABLE_SQL,
        CREATE_INDEX_SQL,
        IMMUTABILITY_GUARD_SQL,
    ]
