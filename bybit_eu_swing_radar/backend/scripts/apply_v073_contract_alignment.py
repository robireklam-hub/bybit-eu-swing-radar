from __future__ import annotations

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}: {old[:140]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_all_checked(path: Path, old: str, new: str, expected: int) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{path}: expected {expected} matches, found {count}: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


# Flow feature remains 0.7.2.2, but its parent day-trade strategy is now 0.7.3.
flow = BACKEND / "flow_context.py"
replace_once(
    flow,
    "Context-only enrichment. This module does NOT change the v0.7.2 STRICT gates,",
    "Context-only enrichment. This module does NOT change the v0.7.3 STRICT gates,",
)
replace_all_checked(
    flow,
    '"strategy_version": "0.7.2"',
    '"strategy_version": "0.7.3"',
    2,
)
replace_once(
    flow,
    '"Flow context is informational only and does not change v0.7.2 STRICT gates or trade decisions.",',
    '"Flow context is informational only and does not change v0.7.3 STRICT gates or trade decisions.",',
)

# API application version follows the day-trade strategy release. Flow feature version is separate.
app = BACKEND / "app" / "main.py"
replace_once(app, 'version="0.7.2.2",', 'version="0.7.3",')
replace_once(
    app,
    'description="Read-only cached USDC swing/day scanner with journaling, replay, diagnostics and context-only derivatives flow enrichment.",',
    'description="Read-only cached USDC swing/day scanner; day-trade strategy v0.7.3 with context-only derivatives Flow feature v0.7.2.2.",',
)

# GPT Action contract: preserve swing semantics, align only day-trade/Flow release wording.
openapi = ROOT / "action" / "openapi.yaml"
replace_once(openapi, "  version: 0.7.2.2\n", "  version: 0.7.3\n")
replace_once(
    openapi,
    "  description: Bybit EU Trading Radar v0.7.2 strategy with v0.7.2.1 compact audit\n    and v0.7.2.2 context-only derivatives OI/funding flow enrichment.\n",
    "  description: Bybit EU Trading Radar with day-trade strategy v0.7.3, compact audit,\n    and context-only derivatives OI/funding Flow feature v0.7.2.2.\n",
)
replace_once(
    openapi,
    "      summary: Return the cached day-trade scan using 4H/1H context, 15m setup and\n        5m trigger.\n",
    "      summary: Return the cached v0.7.3 day-trade scan using 4H/1H context and\n        closed 5m sweep/reclaim/structure confirmation with closed 15m confirmation.\n",
)
replace_once(
    openapi,
    "      description: Bybit global linear derivatives plus already-cached Coinalyze secondary\n        context. This endpoint does not change v0.7.2 STRICT gates or prove Bybit\n        EU execution conditions.\n",
    "      description: Bybit global linear derivatives plus already-cached Coinalyze secondary\n        context. This endpoint does not change v0.7.3 STRICT gates or prove Bybit\n        EU execution conditions.\n",
)

# Agent instructions are swing-first. Add a separate day-trade section instead of changing swing 4H rules.
agent = ROOT / "agent" / "AGENT_INSTRUCTIONS_HU.md"
day_section = '''## Day-trade v0.7.3 külön szabályok\n- A swing szabályoktól külön kezeld a `/v1/day-trade/*` endpointokat.\n- Day-trade-ben az authoritative TRADE trigger: lezárt 5m liquidity sweep -> reclaim -> 5m local structure shift, legalább a konfigurált relatív volumen-megerősítéssel, és a confirmation időpontjáig teljesen lezárt 15m struktúra nem lehet ellenirányú.\n- A `timeframe_conflict=true` 4H konfliktus v0.7.3-ban context-only: önmagában nem hard-veto, nem teheti a strict setupot WATCH_ONLY/NO_TRADE státuszúvá. A 4H továbbra is szerepelhet direction/context komponensként; csak a külön conflict-veto szűnt meg.\n- TRADE csak akkor mondható, ha az API `category=STRICT`, `state=TRIGGERED`, `decision=TRADE` értékeket ad. WATCH/ARMED nem belépő.\n- Long végrehajtás továbbra is kizárólag Bybit EU USDC spot. Short továbbra is kizárólag ellenőrzött Bybit EU USDC spot-margin short, pozitív borrowability mellett.\n- RR, structural-barrier/target-path, likviditás, spread és score gate-ek továbbra is authoritative hard gate-ek.\n- OI/funding/Flow továbbra is context-only; a Flow feature verziója v0.7.2.2, a day-trade stratégia verziója v0.7.3.\n\n'''
replace_once(agent, "## Kötelező adatfegyelem\n", day_section + "## Kötelező adatfegyelem\n")

# Keep the swing spec intact; append a day-trade-specific release annex.
spec = ROOT / "BACKEND_SPEC_HU.md"
text = spec.read_text(encoding="utf-8")
marker = "## 13. Day-trade v0.7.3 kiegészítés"
if marker in text:
    raise RuntimeError("BACKEND_SPEC_HU.md already contains the v0.7.3 annex")
annex = '''\n\n## 13. Day-trade v0.7.3 kiegészítés\nEz a fejezet kizárólag a day-trade motorra vonatkozik; a fenti swing 1D/4H trigger-szabályokat nem módosítja.\n\n- Stratégia verzió: `0.7.3`. A külön derivatives Flow feature verziója változatlanul `0.7.2.2`.\n- Authoritative day-trade trigger: lezárt 5m liquidity sweep -> reclaim -> 5m local structure shift -> a confirmation időpontjáig teljesen lezárt 15m nem-ellenirányú struktúra -> volume confirmation.\n- Entry: a 5m structure-confirmation gyertya záróára. Stop/invalidation: a sweep extreme.\n- A 4H `timeframe_conflict` diagnosztikai/context mező marad, de nem hard-veto a strict eligibilityre vagy executionre. A 4H technikai komponensek továbbra is részt vehetnek a direction/context számításban.\n- Változatlan hard gate-ek: USDC-only execution, spot long, exact USDC spot-margin borrowable short, likviditás/spread, core score minimumok, költség utáni RR és structural-barrier/target-path.\n- A korábbi egyszerű 12x5m breakout önmagában nem adhat `TRADE` döntést. WATCH/ARMED státusz előre jelezhet közelgő setupot, de `TRIGGERED/TRADE` csak a teljes sweep-confirmation szekvenciával lehetséges.\n- Journal és historical replay `strategy_version=0.7.3`, ezért a v0.7.2 mintákkal nem keverhető.\n- OI/funding/Flow context-only marad, nem módosítja a strict gate-eket.\n'''
spec.write_text(text.rstrip() + annex + "\n", encoding="utf-8")

print("v0.7.3 contract alignment applied")
