from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "bybit_eu_swing_radar" / "backend"


def read(path):
    return path.read_text(encoding="utf-8")


def write(path, text):
    path.write_text(text, encoding="utf-8")


def once(path, old, new):
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:120]!r}")
    write(path, text.replace(old, new, 1))


def all_exact(path, old, new, expected):
    text = read(path)
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{path}: expected {expected} matches, found {count}: {old!r}")
    write(path, text.replace(old, new))


# Flow payload follows the live parent strategy while the feature version remains 0.7.2.2.
flow = BACKEND / "flow_context.py"
all_exact(flow, '"strategy_version": "0.7.5"', '"strategy_version": "0.7.6"', 2)

# Freeze old v0.7.5 trigger tests explicitly instead of letting them follow the new live default.
impulse = BACKEND / "test_day_impulse_breakout_trigger.py"
text = read(impulse)
for func in (
    "test_v075_immediate_next_closed_5m_bar_cannot_erase_valid_breakout",
    "test_v075_follow_through_invalidates_only_if_original_boundary_is_lost",
):
    start = text.index(f"def {func}")
    next_def = text.find("\ndef ", start + 4)
    end = len(text) if next_def < 0 else next_def
    block = text[start:end]
    needle = '        datetime(2026, 8, 20, tzinfo=timezone.utc),\n'
    if block.count(needle) != 1:
        raise RuntimeError(f"{func}: expected one datetime call")
    block = block.replace(
        needle,
        needle + '        strategy_version="0.7.5",\n',
        1,
    )
    text = text[:start] + block + text[end:]
write(impulse, text)

# Freeze v0.7.5 journal dedupe semantics including the historical signal-key namespace.
dedupe = BACKEND / "test_day_breakout_journal_dedupe_v075.py"
once(
    dedupe,
    '    monkeypatch.setattr(day_worker,"latest_bar_sweep_setup",lambda *a,**k:None)\n',
    '    monkeypatch.setattr(day_worker,"latest_bar_sweep_setup",lambda *a,**k:None)\n'
    '    monkeypatch.setattr(journal_core,"STRATEGY_VERSION","0.7.5")\n',
)
once(
    dedupe,
    '    c0=build_day_candidate(a0,"long",datetime(2026,8,20,tzinfo=timezone.utc))\n'
    '    c1=build_day_candidate(a1,"long",datetime(2026,8,20,tzinfo=timezone.utc))\n',
    '    c0=build_day_candidate(a0,"long",datetime(2026,8,20,tzinfo=timezone.utc),strategy_version="0.7.5")\n'
    '    c1=build_day_candidate(a1,"long",datetime(2026,8,20,tzinfo=timezone.utc),strategy_version="0.7.5")\n',
)

# Version-contract tests: current live moves to v0.7.6, historical replay remains v0.7.5.
sweep_test = BACKEND / "test_day_sweep_v073.py"
once(
    sweep_test,
    'def test_v073_and_v074_history_are_frozen_while_current_strategy_is_v075():\n'
    '    assert day_worker.LEGACY_DAY_STRATEGY_VERSION == "0.7.3"\n'
    '    assert day_worker.IMPULSE_DAY_STRATEGY_VERSION == "0.7.4"\n'
    '    assert day_worker.DAY_STRATEGY_VERSION == "0.7.5"\n'
    '    assert journal.STRATEGY_VERSION == "0.7.5"\n'
    '    assert backtest.STRATEGY_VERSION == "0.7.5"\n',
    'def test_v073_v074_v075_history_are_frozen_while_current_strategy_is_v076():\n'
    '    assert day_worker.LEGACY_DAY_STRATEGY_VERSION == "0.7.3"\n'
    '    assert day_worker.IMPULSE_DAY_STRATEGY_VERSION == "0.7.4"\n'
    '    assert day_worker.V075_DAY_STRATEGY_VERSION == "0.7.5"\n'
    '    assert day_worker.DAY_STRATEGY_VERSION == "0.7.6"\n'
    '    assert journal.STRATEGY_VERSION == "0.7.6"\n'
    '    assert backtest.STRATEGY_VERSION == "0.7.5"\n',
)

hotpath = BACKEND / "test_journal_schema_hotpath.py"
once(
    hotpath,
    '    assert journal.STRATEGY_VERSION == journal_core.STRATEGY_VERSION == "0.7.5"',
    '    assert journal.STRATEGY_VERSION == journal_core.STRATEGY_VERSION == "0.7.6"',
)

version_iso = BACKEND / "test_v073_version_isolation.py"
once(version_iso, 'def test_repository_reads_current_v075_journal_only():', 'def test_repository_reads_current_v076_journal_only():')
once(version_iso, '    assert \'CURRENT_DAY_STRATEGY_VERSION = "0.7.5"\' in text\n', '    assert \'CURRENT_DAY_STRATEGY_VERSION = "0.7.6"\' in text\n')
once(version_iso, '    assert \'does NOT change the v0.7.5 STRICT gates\' in flow', '    assert \'does NOT change the v0.7.6 STRICT gates\' in flow')

# Action OpenAPI v0.7.6 + first-class setup/entry/hard-stop fields.
openapi = ROOT / "bybit_eu_swing_radar" / "action" / "openapi.yaml"
once(openapi, '  version: 0.7.5\n', '  version: 0.7.6\n')
once(
    openapi,
    '  description: Bybit EU Trading Radar with day-trade strategy v0.7.5, compact audit,\n',
    '  description: Bybit EU Trading Radar with day-trade strategy v0.7.6, separated setup/entry state, compact audit,\n',
)
once(
    openapi,
    '        context. This endpoint does not change v0.7.5 STRICT gates or prove Bybit\n',
    '        context. This endpoint does not change v0.7.6 setup/entry gates or prove Bybit\n',
)

candidate_anchor = '''        setup_type:\n          type: string\n        last_price:\n          type: number\n'''
candidate_new = '''        setup_type:\n          type: string\n        setup_state:\n          type:\n          - string\n          - 'null'\n          enum:\n          - VALID\n          - INVALID\n          - null\n          description: Technical directional setup validity; independent of current RR/barrier entry readiness.\n        entry_state:\n          type:\n          - string\n          - 'null'\n          enum:\n          - NO_SETUP\n          - EXECUTION_BLOCKED\n          - BLOCKED_BY_BARRIER\n          - RR_NOT_READY\n          - ENTRY_CONFIRMED\n          - ENTRY_PROVISIONAL\n          - ENTRY_TOO_EXTENDED\n          - WAIT_TRIGGER\n          - null\n        execution_valid:\n          type:\n          - boolean\n          - 'null'\n        rr_valid:\n          type:\n          - boolean\n          - 'null'\n        reference_entry:\n          type:\n          - number\n          - 'null'\n          description: Fresh current reference used for stop/target/RR geometry; not necessarily the original breakout boundary.\n        breakout_context:\n          type:\n          - object\n          - 'null'\n          additionalProperties: true\n        hard_stop:\n          type:\n          - object\n          - 'null'\n          properties:\n            price:\n              type: number\n            activation:\n              type: string\n            requires_candle_close:\n              type: boolean\n            condition:\n              type: string\n          additionalProperties: true\n        structure_invalidation:\n          type:\n          - object\n          - 'null'\n          properties:\n            timeframe:\n              type: string\n            condition:\n              type: string\n            requires_candle_close:\n              type: boolean\n          additionalProperties: true\n        last_price:\n          type: number\n'''
once(openapi, candidate_anchor, candidate_new)

trigger_anchor = '''        triggered:\n          type: boolean\n      additionalProperties: true\n    DayTradeMetrics:\n'''
trigger_new = '''        triggered:\n          type: boolean\n        route:\n          type:\n          - string\n          - 'null'\n        model:\n          type:\n          - string\n          - 'null'\n        event_bar_time:\n          type:\n          - string\n          - 'null'\n        age_bars:\n          type:\n          - integer\n          - 'null'\n        validity_bars:\n          type:\n          - integer\n          - 'null'\n        boundary_held:\n          type:\n          - boolean\n          - 'null'\n      additionalProperties: true\n    DayTradeMetrics:\n'''
once(openapi, trigger_anchor, trigger_new)

metrics_anchor = '''        distance_to_trigger_atr_5m:\n          type: number\n        assumed_round_trip_cost_bps:\n'''
metrics_new = '''        distance_to_trigger_atr_5m:\n          type: number\n        setup_valid:\n          type: boolean\n        entry_state:\n          type: string\n        reference_entry:\n          type: number\n        breakout_origin_price:\n          type: number\n        entry_geometry_mode:\n          type: string\n          enum:\n          - FRESH_CURRENT_REFERENCE\n          - ORIGIN_TRIGGER_REFERENCE\n        assumed_round_trip_cost_bps:\n'''
once(openapi, metrics_anchor, metrics_new)

# Replace the complete day-trade agent policy block with v0.7.6 semantics.
agent = ROOT / "bybit_eu_swing_radar" / "agent" / "AGENT_INSTRUCTIONS_HU.md"
text = read(agent)
start = text.index("## Day-trade v0.7.5 külön szabályok")
end = text.index("\n## Ticker- és kérdésscope feloldás", start)
new_block = '''## Day-trade v0.7.6 külön szabályok
- A swing szabályoktól külön kezeld a `/v1/day-trade/*` endpointokat.
- A day-trade válaszban **külön kezeld a setup létezését és a jelenlegi belépő végrehajthatóságát**. `setup_state=VALID` azt jelenti, hogy van technikailag érvényes long/short setup akkor is, ha a jelenlegi entry `BLOCKED_BY_BARRIER`, `RR_NOT_READY` vagy `ENTRY_TOO_EXTENDED`.
- Soha ne fordíts egy `setup_state=VALID` day setupot pusztán barrier vagy aktuális RR miatt úgy, hogy „nincs long/short setup”. Ilyenkor mondd ki: **VALID SETUP, de a jelenlegi entry nem kész**, és nevezd meg az `entry_state` okát.
- TRADE csak akkor mondható, ha az API `category=STRICT`, `state=TRIGGERED`, `decision=TRADE` és `entry_state=ENTRY_CONFIRMED` értékeket ad. `ENTRY_PROVISIONAL`, WATCH vagy ARMED nem automatikus végrehajtási engedély.
- A v0.7.6 direct breakout **setup-kontextusa nem jár le fixen két lezárt 5m gyertya után**. Addig maradhat technikailag aktív, amíg az eredeti breakout boundary minden későbbi lezárt 5m gyertyán tart. A régi breakout boundary az origin/context; a jelenlegi entry geometriát a backend friss `reference_entry` alapján számolja újra.
- A `trigger.price` és a `reference_entry` nem feltétlenül azonos. Belépő/RR/stop értelmezésénél a friss `reference_entry`, `entry_zone`, `stop`, `targets`, `expected_rr` és target-path mezők az authoritative értékek.
- A `hard_stop` kockázati stop. Ha `hard_stop.requires_candle_close=false`, **nem kell 5m gyertyazárást megvárni**: az ár touch/cross aktiválja. Ezt ne keverd össze a külön `structure_invalidation` feltétellel, amely például a 15m higher-low/lower-high struktúra elvesztését jelenti.
- `ENTRY_TOO_EXTENDED` esetén van setup, de ne chase-eld; várj friss retestre/pullbackra és újraszámolt entryre.
- `BLOCKED_BY_BARRIER` esetén van setup, de az aktuális target-path blokkolt. A barrier későbbi áttörése után csak friss entry/stop/target/RR alapján értékelj, a régi entry-zónát ne örököld.
- A `timeframe_conflict=true` 4H konfliktus továbbra is context-only: önmagában nem hard-veto, és nem rejtheti el a technikailag valid day setupot.
- A closed-5m breakout/sweep továbbra is authoritative **megerősített** entry út. A rövidebb intrabar/provisional acceptance jelenleg research-only; ne változtasd önállóan TRADE döntéssé.
- Long végrehajtás kizárólag Bybit EU USDC spot. Short kizárólag ellenőrzött Bybit EU USDC spot-margin short, pozitív borrowability mellett.
- OI/funding/Flow továbbra is context-only; a Flow feature verziója v0.7.2.2, a live day-trade stratégia verziója v0.7.6.
'''
write(agent, text[:start] + new_block + text[end:])

# Update contract tests to the new current version while preserving old historical checks.
contract = BACKEND / "test_v073_contract_alignment.py"
text = read(contract)
text = text.replace("test_flow_feature_keeps_0722_but_parent_strategy_is_075", "test_flow_feature_keeps_0722_but_parent_strategy_is_076")
text = text.replace('payload["strategy_version"] == "0.7.5"', 'payload["strategy_version"] == "0.7.6"')
text = text.replace('"v0.7.5 STRICT gates"', '"v0.7.6 STRICT gates"')
text = text.replace("test_fastapi_release_source_declares_075", "test_fastapi_release_source_declares_076")
text = text.replace("'version=\"0.7.5\"'", "'version=\"0.7.6\"'")
text = text.replace('"day-trade strategy v0.7.5"', '"day-trade strategy v0.7.6"')
text = text.replace("test_openapi_contract_describes_v075_day_trigger_and_0722_flow", "test_openapi_contract_describes_v076_day_state_and_0722_flow")
text = text.replace('"version: 0.7.5"', '"version: 0.7.6"')
text = text.replace('"closed 5m 12-bar range breakout OR sweep/reclaim/structure confirmation"', '"separated setup/entry state"')
text = text.replace('"does not change v0.7.5 STRICT gates"', '"does not change v0.7.6 setup/entry gates"')
text = text.replace("test_agent_keeps_swing_trigger_and_adds_separate_day_v075_rules", "test_agent_keeps_swing_trigger_and_adds_separate_day_v076_rules")
text = text.replace('"## Day-trade v0.7.5 külön szabályok"', '"## Day-trade v0.7.6 külön szabályok"')
if "setup_state=VALID" not in text:
    text = text.replace('    assert "category=STRICT" in text\n', '    assert "category=STRICT" in text\n    assert "setup_state=VALID" in text\n    assert "hard_stop.requires_candle_close=false" in text\n')
write(contract, text)

print("v0.7.6 contract patch applied")
