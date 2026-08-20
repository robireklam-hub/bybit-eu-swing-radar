# Regression contract for explicit agent market-context visibility and barrier semantics.
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent" / "AGENT_INSTRUCTIONS_HU.md"
OPENAPI = ROOT / "action" / "openapi.yaml"


def _schema_block(text: str, name: str) -> str:
    marker = f"    {name}:\n"
    start = text.index(marker)
    rest = text[start + len(marker):]
    candidates = [
        rest.find(f"\n    {other}:\n")
        for other in (
            "ScanResponse", "Setup", "PriceCondition", "WatchlistResponse",
            "MomentumResponse", "CompactCandidate", "TopCandidatesResponse",
            "DayTradeCandidate", "DayTradeScanResponse", "DayTradeTopCandidatesResponse",
            "SourceQuality", "JournalAggregate", "DayTradeStatusResponse",
            "DayTradeSymbolAuditResponse", "DayTradeFlowContextResponse",
            "MarketContextMarketImpulse", "MarketContextGeopolitical",
            "MarketContextMacroLiquidity", "MarketContextAlerts"
        )
        if other != name and rest.find(f"\n    {other}:\n") >= 0
    ]
    end = min(candidates) if candidates else len(rest)
    return rest[:end]


def test_agent_requires_explicit_elevated_market_context_reporting():
    text = AGENT.read_text(encoding="utf-8")
    assert "## Kötelező market-context riportálás" in text
    assert "# MARKET CONTEXT WARNING — <warning_level>" in text
    assert "market_impulse.state" in text
    assert "geopolitical.state" in text
    assert "macro_liquidity.state" in text
    assert "ELEVATED/HIGH figyelmeztetést ne süllyessz egy mellékmondatba" in text
    assert "NO_TRADE" in text and "WATCH_ONLY" in text


def test_agent_does_not_expire_confirmed_barrier_on_next_candle():
    text = AGENT.read_text(encoding="utf-8")
    assert "nem szűnik meg pusztán attól, hogy lezár még egy 5m gyertya" in text
    assert "target_path_valid=false" in text
    assert "ne találj ki saját barrier-expiry szabályt" in text


def test_openapi_exposes_market_context_as_first_class_response_field():
    text = OPENAPI.read_text(encoding="utf-8")
    assert "    MarketContextAlerts:\n" in text
    assert "    MarketContextGeopolitical:\n" in text
    assert "    MarketContextMacroLiquidity:\n" in text
    assert "    MarketContextMarketImpulse:\n" in text
    for schema in (
        "MarketRegime", "ScanResponse", "Setup", "WatchlistResponse",
        "MomentumResponse", "TopCandidatesResponse", "DayTradeCandidate",
        "DayTradeScanResponse", "DayTradeStatusResponse",
        "DayTradeSymbolAuditResponse", "DayTradeFlowContextResponse"
    ):
        assert "market_context_alerts:" in _schema_block(text, schema), schema
    assert "warning_level:" in _schema_block(text, "MarketContextAlerts")
    assert "mandatory_user_warning:" in _schema_block(text, "MarketContextAlerts")
    assert "geopolitical:" in _schema_block(text, "MarketContextAlerts")


def test_openapi_market_context_contract_parses_as_yaml():
    document = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    schemas = document["components"]["schemas"]
    alerts = schemas["MarketContextAlerts"]
    assert alerts["properties"]["warning_level"]["enum"] == [
        "NORMAL", "ELEVATED", "HIGH", "UNKNOWN", "UNAVAILABLE"
    ]
    assert alerts["properties"]["geopolitical"]["$ref"].endswith("/MarketContextGeopolitical")
    assert alerts["properties"]["macro_liquidity"]["$ref"].endswith("/MarketContextMacroLiquidity")


def test_openapi_day_scan_summary_matches_live_v075_strategy():
    text = OPENAPI.read_text(encoding="utf-8")
    assert "Return the cached v0.7.5 day-trade scan" in text
    assert "Return the cached v0.7.3 day-trade scan" not in text
