from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "bybit_eu_swing_radar" / "backend"
AGENT = ROOT / "bybit_eu_swing_radar" / "agent" / "AGENT_INSTRUCTIONS_HU.md"


def one(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1, found {count}")
    path.write_text(text.replace(old, new, 1))

p = BACKEND / "test_day_v076_action_contract.py"
one(p, "def test_openapi_exposes_v076_setup_entry_and_hard_stop_contract():", "def test_openapi_exposes_v077_persistent_recommendation_and_hard_stop_contract():", "action test name")
one(p, '    assert "version: 0.7.6" in text\n    assert "separated setup/entry state" in text', '    assert "version: 0.7.7" in text\n    assert "persistent confirmed-breakout recommendation state" in text', "action openapi assertions")
one(p, "def test_agent_must_report_valid_setup_separately_from_entry_readiness():", "def test_agent_must_preserve_confirmed_recommendation_unless_real_invalidation_occurs():", "agent test name")
one(p, '    assert "## Day-trade v0.7.6 külön szabályok" in text', '    assert "## Day-trade v0.7.7 külön szabályok" in text', "agent heading assertion")
text = p.read_text()
anchor = '    assert "nem jár le fixen két lezárt 5m gyertya után" in text\n'
if anchor not in text:
    raise RuntimeError("agent persistence assertion anchor missing")
text = text.replace(anchor, anchor + '    assert "önmagában nem minősítheti vissza" in text\n', 1)
p.write_text(text)

p = BACKEND / "test_v073_version_isolation.py"
one(p, "assert 'does NOT change the v0.7.6 STRICT gates' in flow", "assert 'does NOT change the v0.7.7 STRICT gates' in flow", "flow isolation expected current parent")

p = AGENT
one(p, "## Day-trade v0.7.6 külön szabályok", "## Day-trade v0.7.7 külön szabályok", "agent v077 heading")
one(p, "A v0.7.6 direct breakout **setup-kontextusa nem jár le fixen két lezárt 5m gyertya után**.", "A v0.7.7 direct breakout **megerősített ajánlása és setup-kontextusa nem jár le fixen két lezárt 5m gyertya után**.", "agent persistent recommendation intro")
text = p.read_text()
anchor = "A régi breakout boundary az origin/context; a jelenlegi entry geometriát a backend friss `reference_entry` alapján számolja újra.\n"
insert = anchor + "- Egy következő lezárt 5m gyertya **puszta megérkezése önmagában nem minősítheti vissza** az egyébként továbbra is valid `ENTRY_CONFIRMED` / `TRIGGERED` / `TRADE` ajánlást WATCH/ARMED állapotba. A megerősítés addig marad aktív, amíg az eredeti breakout boundary tart és a frissen újraszámolt setup-, execution-, RR-, target-path- és barrier-gate-ek érvényesek. Csak valódi invalidáció — például boundary-vesztés, setup-romlás, execution/liquidity blokk vagy érvénytelen RR/target path — veheti vissza az ajánlást.\n"
if anchor not in text:
    raise RuntimeError("agent insertion anchor missing")
text = text.replace(anchor, insert, 1)
text = text.replace("A `timeframe_conflict=true` 4H konfliktus továbbra is context-only", "A `timeframe_conflict=true` 4H konfliktus továbbra is context-only", 1)
text = text.replace("a live day-trade stratégia verziója v0.7.6.", "a live day-trade stratégia verziója v0.7.7.", 1)
p.write_text(text)

print("DAY_V077_AGENT_CONTRACT_FINALIZED")
