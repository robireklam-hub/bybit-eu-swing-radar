from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "bybit_eu_swing_radar" / "backend"
ACTION = ROOT / "bybit_eu_swing_radar" / "action" / "openapi.yaml"


def replace(text: str, old: str, new: str, *, count: int | None = None, label: str = "replace") -> str:
    found = text.count(old)
    expected = count if count is not None else 1
    if found != expected:
        raise RuntimeError(f"{label}: expected {expected}, found {found}")
    return text.replace(old, new, expected)

# Flow is still context-only, but its parent live strategy identity must match current day v0.7.7.
p = BACKEND / "flow_context.py"
t = p.read_text()
t = replace(t, "This module does NOT change the v0.7.6 STRICT gates,", "This module does NOT change the v0.7.7 STRICT gates,", label="flow doc")
t = replace(t, '"strategy_version": "0.7.6"', '"strategy_version": "0.7.7"', count=2, label="flow payload versions")
t = replace(t, '"Flow context is informational only and does not change v0.7.6 STRICT gates or trade decisions."', '"Flow context is informational only and does not change v0.7.7 STRICT gates or trade decisions."', label="flow note")
p.write_text(t)

# Action contract must identify the same current release. Historical cohort text is not globally rewritten.
p = ACTION
t = p.read_text()
t = replace(t, "  version: 0.7.6", "  version: 0.7.7", label="openapi version")
t = replace(t, "day-trade strategy v0.7.6, separated setup/entry state", "day-trade strategy v0.7.7, persistent confirmed-breakout recommendation state", label="openapi description")
# Current live references only; avoid touching explicitly historical/research cohort names.
t = t.replace("does not change v0.7.6 STRICT gates", "does not change v0.7.7 STRICT gates")
p.write_text(t)

# Current-version contract tests move to 0.7.7, while historical v0.7.6 behavior remains explicit.
p = BACKEND / "test_day_sweep_v073.py"
t = p.read_text()
t = replace(t, "def test_v073_v074_v075_history_are_frozen_while_current_strategy_is_v076():", "def test_v073_v074_v075_v076_history_are_frozen_while_current_strategy_is_v077():", label="sweep version test name")
t = replace(t, '    assert day_worker.V075_DAY_STRATEGY_VERSION == "0.7.5"\n    assert day_worker.DAY_STRATEGY_VERSION == "0.7.6"\n    assert journal.STRATEGY_VERSION == "0.7.6"', '    assert day_worker.V075_DAY_STRATEGY_VERSION == "0.7.5"\n    assert day_worker.V076_DAY_STRATEGY_VERSION == "0.7.6"\n    assert day_worker.DAY_STRATEGY_VERSION == "0.7.7"\n    assert journal.STRATEGY_VERSION == "0.7.7"', label="sweep current versions")
p.write_text(t)

p = BACKEND / "test_day_v076_version_isolation.py"
t = p.read_text()
t = replace(t, "def test_live_day_version_moves_to_v076_only():\n    assert day_worker.DAY_STRATEGY_VERSION == \"0.7.6\"\n    assert repository.CURRENT_DAY_STRATEGY_VERSION == \"0.7.6\"\n    assert journal_core.STRATEGY_VERSION == \"0.7.6\"", "def test_live_day_version_moves_to_v077_while_v076_is_frozen():\n    assert day_worker.V076_DAY_STRATEGY_VERSION == \"0.7.6\"\n    assert day_worker.DAY_STRATEGY_VERSION == \"0.7.7\"\n    assert repository.CURRENT_DAY_STRATEGY_VERSION == \"0.7.7\"\n    assert journal_core.STRATEGY_VERSION == \"0.7.7\"", label="version isolation current")
p.write_text(t)

p = BACKEND / "test_journal_schema_hotpath.py"
t = p.read_text()
t = replace(t, 'assert journal.STRATEGY_VERSION == journal_core.STRATEGY_VERSION == "0.7.6"', 'assert journal.STRATEGY_VERSION == journal_core.STRATEGY_VERSION == "0.7.7"', label="journal current version")
p.write_text(t)

p = BACKEND / "test_v073_contract_alignment.py"
t = p.read_text()
# Flow payload/current API are current release; OpenAPI current release follows too.
t = replace(t, "def test_flow_feature_keeps_0722_but_parent_strategy_is_076():", "def test_flow_feature_keeps_0722_but_parent_strategy_is_077():", label="flow test name")
t = replace(t, 'assert payload["strategy_version"] == "0.7.6"', 'assert payload["strategy_version"] == "0.7.7"', label="flow expected version")
t = replace(t, 'assert any("v0.7.6 STRICT gates" in note for note in payload["notes"])', 'assert any("v0.7.7 STRICT gates" in note for note in payload["notes"])', label="flow expected note")
t = replace(t, "def test_fastapi_release_source_declares_076():", "def test_fastapi_release_source_declares_077():", label="fastapi test name")
t = replace(t, 'assert \'version="0.7.6"\' in text\n    assert "day-trade strategy v0.7.6" in text', 'assert \'version="0.7.7"\' in text\n    assert "day-trade strategy v0.7.7" in text', label="fastapi expected")
t = replace(t, "def test_openapi_contract_describes_v076_day_state_and_0722_flow():", "def test_openapi_contract_describes_v077_day_state_and_0722_flow():", label="openapi test name")
t = replace(t, 'assert "version: 0.7.6" in text\n    assert "day-trade strategy v0.7.6" in text\n    assert "Flow feature v0.7.2.2" in text\n    assert "separated setup/entry state" in text', 'assert "version: 0.7.7" in text\n    assert "day-trade strategy v0.7.7" in text\n    assert "Flow feature v0.7.2.2" in text\n    assert "persistent confirmed-breakout recommendation state" in text', label="openapi expected")
p.write_text(t)

p = BACKEND / "test_v073_version_isolation.py"
t = p.read_text()
t = replace(t, "def test_repository_reads_current_v076_journal_only():", "def test_repository_reads_current_v077_journal_only():", label="repo version test name")
t = replace(t, 'assert \'CURRENT_DAY_STRATEGY_VERSION = "0.7.6"\' in text', 'assert \'CURRENT_DAY_STRATEGY_VERSION = "0.7.7"\' in text', label="repo expected version")
p.write_text(t)

# Preserve v0.7.6 regression meaning by explicitly invoking that historical version.
p = BACKEND / "test_day_v076_live_worker_regression.py"
t = p.read_text()
# Two build_day_candidate calls in this file are historical v0.7.6 expectations.
needle = '        datetime(2026, 8, 21, tzinfo=timezone.utc),\n    )'
if t.count(needle) < 2:
    raise RuntimeError(f"v076 regression calls: expected >=2, found {t.count(needle)}")
t = t.replace(needle, '        datetime(2026, 8, 21, tzinfo=timezone.utc),\n        strategy_version="0.7.6",\n    )', 2)
p.write_text(t)

print("DAY_V077_CONTRACTS_FINALIZED")
