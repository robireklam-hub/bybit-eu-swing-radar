from app.research_signal_context_freeze_api import SCHEMA_SQL as FREEZE_SCHEMA_SQL
from research.research_snapshot_history import SCHEMA_SQL as HISTORY_SCHEMA_SQL
from research.research_trial_registry import TRIAL_REGISTRY_SCHEMA_SQL


def _assert_catalog_guarded_append_only(schema: str, table: str, function: str) -> None:
    assert f"CREATE TABLE IF NOT EXISTS {table}" in schema
    assert "to_regprocedure(current_schema()" in schema
    assert function in schema
    assert "SELECT 1 FROM pg_trigger" in schema
    assert f"BEFORE UPDATE OR DELETE ON {table}" in schema
    assert f"BEFORE TRUNCATE ON {table}" in schema
    assert "FOR EACH ROW EXECUTE FUNCTION" in schema
    assert "FOR EACH STATEMENT EXECUTE FUNCTION" in schema
    # Hot paths must not churn catalog objects on every schema ensure.
    assert "DROP TRIGGER" not in schema
    assert "CREATE OR REPLACE FUNCTION" not in schema


def test_trial_registry_has_db_role_mutation_guards():
    _assert_catalog_guarded_append_only(
        TRIAL_REGISTRY_SCHEMA_SQL,
        "research_trial_registry",
        "reject_research_trial_registry_mutation",
    )


def test_raw_snapshot_history_has_db_role_mutation_guards():
    _assert_catalog_guarded_append_only(
        HISTORY_SCHEMA_SQL,
        "research_snapshot_history",
        "reject_research_snapshot_history_mutation",
    )


def test_signal_context_freezes_have_db_role_mutation_guards():
    _assert_catalog_guarded_append_only(
        FREEZE_SCHEMA_SQL,
        "research_signal_context_freezes",
        "reject_research_signal_context_freezes_mutation",
    )
