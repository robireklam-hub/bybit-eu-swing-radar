from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "bybit_eu_swing_radar" / "agent" / "AGENT_INSTRUCTIONS_HU.md"
OPENAPI = ROOT / "bybit_eu_swing_radar" / "action" / "openapi.yaml"
TEST = ROOT / "bybit_eu_swing_radar" / "backend" / "test_agent_market_context_reporting_contract.py"


def insert_once(text: str, anchor: str, addition: str, marker: str) -> str:
    if marker in text:
        return text
    if anchor not in text:
        raise RuntimeError(f"missing insertion anchor for {marker!r}")
    return text.replace(anchor, addition + anchor, 1)


def patch_agent() -> None:
    text = AGENT.read_text(encoding="utf-8")

    market_section = """
## Kötelező market-context riportálás

Minden olyan Action-válasznál, amely tartalmaz `market_context_alerts` objektumot, azt kötelező értelmezni. Nem hagyható figyelmen kívül azért, mert a setup `NO_TRADE`, `WATCH_ONLY`, alacsony pontszámú vagy target-path által blokkolt.

Ha `market_context_alerts.warning_level` értéke `ELEVATED` vagy `HIGH`, vagy `mandatory_user_warning=true`, a trade-értékelés előtt külön, jól látható blokkban add vissza:

`# MARKET CONTEXT WARNING — <warning_level>`

A blokkban add meg legalább:
- `headline`;
- `market_impulse.state` és `max_relative_volume_ratio_5m_15m`;
- `geopolitical.state` és `geopolitical.note`; ha elérhető, a source timestamp és data quality is;
- `macro_liquidity.state` és a releváns note / Fed / RRP / BTC ETF kontextus;
- `external_context_error`, ha nem null;
- az attribúció státuszát (`causal_attribution`).

Ha a geopolitikai state `STALE`, `UNAVAILABLE` vagy `BASELINE_BUILDING`, mondd ki explicit, hogy a külső katalizátor attribúciója hiányos vagy jelenleg nem ellenőrizhető. `NORMAL` warning esetén elég egy rövid market-context sor, de az elérhető geopolitikai státuszt akkor se változtasd meg.

A market-context réteg mindig context-only: önmagában nem módosít score-t, eligibility-t, target-pathot vagy executiont. Emelkedett spot volumenből ne állíts „makro-likviditás injekciót”, és geopolitikai együttmozgásból ne állíts okságot. Különítsd el az **észlelt rövidtávú relatív spot volumenimpulzust** a **bizonyított külső likviditási vagy geopolitikai októl**.

"""
    text = insert_once(
        text,
        "## Döntési modell\n",
        market_section,
        "## Kötelező market-context riportálás",
    )

    barrier_section = """
## Strukturális barrier és target-path jelentése

A már megerősített strukturális barrier nem szűnik meg pusztán attól, hogy lezár még egy 5m gyertya. Ha `target_path_valid=false`, a következő ellenőrzési pontot úgy fogalmazd meg, hogy a piac **érvényesen clear-eli / áttöri-e a barriert, és az API újraszámolva ismét érvényes target-pathot és elfogadható RR-t ad-e**. Ne írd azt, hogy a barrier egyszerűen „megszűnhet” a következő gyertyával.

Mindig az API aktuális `nearest_structural_barrier`, `barrier_source`, `target_path_valid` és `expected_rr_with_barrier` mezőit tekintsd autoritatívnak; ne találj ki saját barrier-expiry szabályt.

"""
    text = insert_once(
        text,
        "## Válaszformátum teljes scan esetén\n",
        barrier_section,
        "## Strukturális barrier és target-path jelentése",
    )

    market_format = """
# MARKET CONTEXT
Ha a válasz tartalmaz `market_context_alerts` mezőt, itt add vissza. `ELEVATED`/`HIGH` vagy `mandatory_user_warning=true` esetén használd a kötelező `# MARKET CONTEXT WARNING — <warning_level>` blokkot a trade-jelöltek előtt. Legalább: warning level, market impulse state/ratio, geopolitical state, macro-liquidity state, headline és attribúciós státusz.

"""
    text = insert_once(
        text,
        "# TOP LONG\n",
        market_format,
        "# MARKET CONTEXT\n",
    )

    single_rule = "- Ha a single-symbol Action-válasz tartalmaz `market_context_alerts` mezőt, az előző kötelező market-context szabály szerint explicit írd ki a státuszt; ELEVATED/HIGH figyelmeztetést ne süllyessz egy mellékmondatba.\n"
    if single_rule not in text:
        anchor = "\nHa az API csak az egyik oldalra ad setupot, a másik oldalra ne találj ki entry/stop/target értékeket.\n"
        if anchor not in text:
            raise RuntimeError("single-symbol reporting anchor missing")
        text = text.replace(anchor, "\n" + single_rule + anchor, 1)

    AGENT.write_text(text, encoding="utf-8")


def schema_block_bounds(text: str, schema_name: str) -> tuple[int, int]:
    marker = f"    {schema_name}:\n"
    start = text.find(marker)
    if start < 0:
        raise RuntimeError(f"OpenAPI schema missing: {schema_name}")
    tail_start = start + len(marker)
    match = re.search(r"(?m)^    [A-Za-z][A-Za-z0-9_]*:\n", text[tail_start:])
    end = tail_start + match.start() if match else len(text)
    return start, end


def add_market_context_property(text: str, schema_name: str) -> str:
    start, end = schema_block_bounds(text, schema_name)
    block = text[start:end]
    if "        market_context_alerts:\n" in block:
        return text
    properties = "      properties:\n"
    offset = block.find(properties)
    if offset < 0:
        raise RuntimeError(f"OpenAPI properties block missing: {schema_name}")
    insert_at = start + offset + len(properties)
    prop = "        market_context_alerts:\n          $ref: '#/components/schemas/MarketContextAlerts'\n"
    return text[:insert_at] + prop + text[insert_at:]


def patch_openapi() -> None:
    text = OPENAPI.read_text(encoding="utf-8")
    text = text.replace(
        "    and context-only derivatives OI/funding Flow feature v0.7.2.2.\n",
        "    and context-only derivatives OI/funding Flow feature v0.7.2.2 plus explicit market-context warnings.\n",
        1,
    )
    text = text.replace(
        "      summary: Return the cached v0.7.3 day-trade scan using 4H/1H context and\n",
        "      summary: Return the cached v0.7.5 day-trade scan using 4H/1H context and\n",
        1,
    )

    for schema_name in (
        "MarketRegime",
        "ScanResponse",
        "Setup",
        "WatchlistResponse",
        "MomentumResponse",
        "TopCandidatesResponse",
        "DayTradeCandidate",
        "DayTradeScanResponse",
        "DayTradeStatusResponse",
        "DayTradeSymbolAuditResponse",
        "DayTradeFlowContextResponse",
    ):
        text = add_market_context_property(text, schema_name)

    context_schemas = """    MarketContextMarketImpulse:
      type: object
      properties:
        state:
          type: string
          enum:
          - NORMAL
          - ELEVATED
          - HIGH
          - UNKNOWN
        max_relative_volume_ratio_5m_15m:
          type:
          - number
          - 'null'
        max_abs_return_15m_pct:
          type:
          - number
          - 'null'
        thresholds:
          type: object
          additionalProperties: true
        interpretation:
          type: string
      additionalProperties: true
    MarketContextGeopolitical:
      type: object
      properties:
        state:
          type: string
          enum:
          - HIGH
          - ELEVATED
          - NORMAL
          - BASELINE_BUILDING
          - STALE
          - UNAVAILABLE
        data_quality:
          type: string
        source:
          type:
          - string
          - 'null'
        source_timestamp:
          type:
          - string
          - 'null'
        source_age_seconds:
          type:
          - number
          - 'null'
        baseline_prior_snapshots:
          type: integer
        baseline_window_hours:
          type: integer
        baseline_min_snapshots:
          type: integer
        metrics:
          type: object
          additionalProperties: true
        prior_baseline_percentiles:
          type: object
          additionalProperties: true
        top_action_countries:
          type: array
          items: {}
        mandatory_warning:
          type: boolean
        causal_attribution:
          type: string
        note:
          type: string
      additionalProperties: true
    MarketContextMacroLiquidity:
      type: object
      properties:
        state:
          type: string
          enum:
          - AVAILABLE
          - UNAVAILABLE
        captured_at:
          type:
          - string
          - 'null'
        fed_total_assets: {}
        overnight_reverse_repo: {}
        btc_etf:
          type: object
          additionalProperties: true
        intraday_causality_supported:
          type: boolean
        note:
          type: string
      additionalProperties: true
    MarketContextAlerts:
      type: object
      properties:
        version:
          type: string
        context_only:
          type: boolean
        hard_gate:
          type: boolean
        score_mutation:
          type: boolean
        eligibility_mutation:
          type: boolean
        execution_mutation:
          type: boolean
        warning_level:
          type: string
          enum:
          - NORMAL
          - ELEVATED
          - HIGH
          - UNKNOWN
          - UNAVAILABLE
        mandatory_user_warning:
          type: boolean
        headline:
          type: string
        market_impulse:
          $ref: '#/components/schemas/MarketContextMarketImpulse'
        geopolitical:
          $ref: '#/components/schemas/MarketContextGeopolitical'
        macro_liquidity:
          $ref: '#/components/schemas/MarketContextMacroLiquidity'
        causal_attribution:
          type: string
        external_context_error:
          type:
          - string
          - 'null'
        reporting_policy:
          type: object
          additionalProperties: true
      additionalProperties: true
"""
    if "    MarketContextAlerts:\n" not in text:
        anchor = "    SourceQuality:\n"
        if anchor not in text:
            raise RuntimeError("OpenAPI SourceQuality insertion anchor missing")
        text = text.replace(anchor, context_schemas + anchor, 1)

    OPENAPI.write_text(text, encoding="utf-8")


def write_contract_test() -> None:
    TEST.write_text(
        '''from pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\nAGENT = ROOT / "agent" / "AGENT_INSTRUCTIONS_HU.md"\nOPENAPI = ROOT / "action" / "openapi.yaml"\n\n\ndef _schema_block(text: str, name: str) -> str:\n    marker = f"    {name}:\\n"\n    start = text.index(marker)\n    rest = text[start + len(marker):]\n    candidates = [\n        rest.find(f"\\n    {other}:\\n")\n        for other in (\n            "ScanResponse", "Setup", "PriceCondition", "WatchlistResponse",\n            "MomentumResponse", "CompactCandidate", "TopCandidatesResponse",\n            "DayTradeCandidate", "DayTradeScanResponse", "DayTradeTopCandidatesResponse",\n            "SourceQuality", "JournalAggregate", "DayTradeStatusResponse",\n            "DayTradeSymbolAuditResponse", "DayTradeFlowContextResponse",\n            "MarketContextMarketImpulse", "MarketContextGeopolitical",\n            "MarketContextMacroLiquidity", "MarketContextAlerts"\n        )\n        if other != name and rest.find(f"\\n    {other}:\\n") >= 0\n    ]\n    end = min(candidates) if candidates else len(rest)\n    return rest[:end]\n\n\ndef test_agent_requires_explicit_elevated_market_context_reporting():\n    text = AGENT.read_text(encoding="utf-8")\n    assert "## Kötelező market-context riportálás" in text\n    assert "# MARKET CONTEXT WARNING — <warning_level>" in text\n    assert "market_impulse.state" in text\n    assert "geopolitical.state" in text\n    assert "macro_liquidity.state" in text\n    assert "ELEVATED/HIGH figyelmeztetést ne süllyessz egy mellékmondatba" in text\n    assert "NO_TRADE" in text and "WATCH_ONLY" in text\n\n\ndef test_agent_does_not_expire_confirmed_barrier_on_next_candle():\n    text = AGENT.read_text(encoding="utf-8")\n    assert "nem szűnik meg pusztán attól, hogy lezár még egy 5m gyertya" in text\n    assert "target_path_valid=false" in text\n    assert "ne találj ki saját barrier-expiry szabályt" in text\n\n\ndef test_openapi_exposes_market_context_as_first_class_response_field():\n    text = OPENAPI.read_text(encoding="utf-8")\n    assert "    MarketContextAlerts:\\n" in text\n    assert "    MarketContextGeopolitical:\\n" in text\n    assert "    MarketContextMacroLiquidity:\\n" in text\n    assert "    MarketContextMarketImpulse:\\n" in text\n    for schema in (\n        "MarketRegime", "ScanResponse", "Setup", "WatchlistResponse",\n        "MomentumResponse", "TopCandidatesResponse", "DayTradeCandidate",\n        "DayTradeScanResponse", "DayTradeStatusResponse",\n        "DayTradeSymbolAuditResponse", "DayTradeFlowContextResponse"\n    ):\n        assert "market_context_alerts:" in _schema_block(text, schema), schema\n    assert "warning_level:" in _schema_block(text, "MarketContextAlerts")\n    assert "mandatory_user_warning:" in _schema_block(text, "MarketContextAlerts")\n    assert "geopolitical:" in _schema_block(text, "MarketContextAlerts")\n\n\ndef test_openapi_day_scan_summary_matches_live_v075_strategy():\n    text = OPENAPI.read_text(encoding="utf-8")\n    assert "Return the cached v0.7.5 day-trade scan" in text\n    assert "Return the cached v0.7.3 day-trade scan" not in text\n''',
        encoding="utf-8",
    )


def main() -> None:
    patch_agent()
    patch_openapi()
    write_contract_test()


if __name__ == "__main__":
    main()
