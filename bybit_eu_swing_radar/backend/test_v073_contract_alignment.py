from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from flow_context import build_flow_payload


BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent
NOW = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)


def _flow_payload() -> dict:
    return build_flow_payload(
        spot_symbol="BTCUSDC",
        setup_payload={
            "data_as_of": NOW.isoformat(),
            "metrics": {
                "return_15m_pct": 0.1,
                "return_1h_pct": 0.2,
                "return_4h_pct": 0.3,
            },
        },
        derivative_instrument={
            "symbol": "BTCUSDT",
            "baseCoin": "BTC",
            "quoteCoin": "USDT",
            "contractType": "LinearPerpetual",
        },
        derivative_ticker={
            "openInterest": "100",
            "openInterestValue": "1000000",
            "fundingRate": "0.0001",
        },
        oi_history=[],
        generated_at=NOW,
    )


def test_flow_feature_keeps_0722_but_parent_strategy_is_075():
    payload = _flow_payload()
    assert payload["strategy_version"] == "0.7.5"
    assert payload["feature_version"] == "0.7.2.2"
    assert any("v0.7.5 STRICT gates" in note for note in payload["notes"])


def test_fastapi_release_source_declares_075():
    text = (BACKEND / "app" / "main.py").read_text(encoding="utf-8")
    assert 'version="0.7.5"' in text
    assert "day-trade strategy v0.7.5" in text
    assert "Flow feature v0.7.2.2" in text


def test_openapi_contract_describes_v075_day_trigger_and_0722_flow():
    text = (ROOT / "action" / "openapi.yaml").read_text(encoding="utf-8")
    assert "version: 0.7.5" in text
    assert "day-trade strategy v0.7.5" in text
    assert "Flow feature v0.7.2.2" in text
    assert "closed 5m 12-bar range breakout OR sweep/reclaim/structure confirmation" in text
    assert "does not change v0.7.5 STRICT gates" in text


def test_agent_keeps_swing_trigger_and_adds_separate_day_v075_rules():
    text = (ROOT / "agent" / "AGENT_INSTRUCTIONS_HU.md").read_text(encoding="utf-8")
    assert "A jelenlegi swing backend 4H lezárt gyertyás triggert ad" in text
    assert "## Day-trade v0.7.5 külön szabályok" in text
    assert "timeframe_conflict=true" in text
    assert "context-only" in text
    assert "category=STRICT" in text
    assert "state=TRIGGERED" in text
    assert "decision=TRADE" in text
    assert "közvetlenül követő lezárt 5m gyertyán is aktív marad" in text
    assert "következő 5m gyertya puszta lezárása nem lehet hard-veto" in text


def test_backend_spec_keeps_historical_v074_and_adds_v075_persistence_annex():
    text = (ROOT / "BACKEND_SPEC_HU.md").read_text(encoding="utf-8")
    assert "A jelenlegi swing worker authoritative triggerje 4H lezárt gyertya" in text
    assert "## 14. Day-trade v0.7.4 kiegészítés" in text
    assert "## 15. Day-trade v0.7.5 breakout-aktiváció" in text
    assert "A 4H `timeframe_conflict` diagnosztikai/context mező marad, de nem hard-veto" in text
    assert "Journal és historical replay `strategy_version=0.7.5`" in text
    assert "közvetlenül követő egy lezárt 5m gyertyán aktív marad" in text
