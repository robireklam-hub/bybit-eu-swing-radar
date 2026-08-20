import asyncio
from types import SimpleNamespace

from app.market_context_compat import (
    install_market_context_compatibility_bridge,
    mirror_mandatory_warning,
)


def _alerts(level="HIGH", mandatory=True):
    return {
        "warning_level": level,
        "mandatory_user_warning": mandatory,
        "headline": "Observed spot-volume impulse; attribution incomplete.",
        "market_impulse": {
            "state": "HIGH",
            "max_relative_volume_ratio_5m_15m": 3.83,
        },
        "geopolitical": {"state": "STALE"},
        "macro_liquidity": {"state": "AVAILABLE"},
        "causal_attribution": "UNCONFIRMED_UNLESS_INDEPENDENTLY_CORROBORATED",
    }


def test_mandatory_warning_is_mirrored_into_old_single_symbol_fields_without_mutating_input():
    original = {
        "symbol": "BTCUSDC",
        "why_now": ["breakout observed"],
        "risks": ["near structural barrier"],
        "market_context_alerts": _alerts(),
    }
    mirrored = mirror_mandatory_warning(original)

    assert original["why_now"] == ["breakout observed"]
    assert original["risks"] == ["near structural barrier"]
    warning = mirrored["why_now"][-1]
    assert warning.startswith("[MARKET_CONTEXT_WARNING:HIGH]")
    assert "market_impulse=HIGH(3.830x)" in warning
    assert "geopolitical=STALE" in warning
    assert "macro_liquidity=AVAILABLE" in warning
    assert "UNCONFIRMED_UNLESS_INDEPENDENTLY_CORROBORATED" in warning
    assert mirrored["risks"][-1] == warning


def test_scan_warning_reaches_known_market_regime_and_candidate_fields():
    payload = {
        "market_regime": {"notes": []},
        "strict_longs": [{"symbol": "BTCUSDC", "why_now": [], "risks": []}],
        "watch_only_shorts": [{"symbol": "ETHUSDC", "why_now": [], "risks": []}],
        "market_context_alerts": _alerts(level="ELEVATED", mandatory=True),
    }
    mirrored = mirror_mandatory_warning(payload)

    warning = mirrored["market_regime"]["notes"][0]
    assert warning.startswith("[MARKET_CONTEXT_WARNING:ELEVATED]")
    assert mirrored["strict_longs"][0]["why_now"] == [warning]
    assert mirrored["strict_longs"][0]["risks"] == [warning]
    assert mirrored["watch_only_shorts"][0]["why_now"] == [warning]


def test_normal_context_is_not_mirrored_into_legacy_fields():
    payload = {
        "symbol": "BTCUSDC",
        "why_now": [],
        "risks": [],
        "market_context_alerts": _alerts(level="NORMAL", mandatory=False),
    }
    mirrored = mirror_mandatory_warning(payload)
    assert mirrored["why_now"] == []
    assert mirrored["risks"] == []


def test_bridge_wraps_canonical_enricher_once():
    calls = []

    async def canonical(result):
        calls.append(result)
        return {
            "symbol": "BTCUSDC",
            "why_now": [],
            "risks": [],
            "market_context_alerts": _alerts(),
        }

    module = SimpleNamespace(enrich_market_response=canonical)
    install_market_context_compatibility_bridge(module)
    first = module.enrich_market_response
    install_market_context_compatibility_bridge(module)
    assert module.enrich_market_response is first

    result = asyncio.run(module.enrich_market_response({"symbol": "BTCUSDC"}))
    assert calls == [{"symbol": "BTCUSDC"}]
    assert result["why_now"][0].startswith("[MARKET_CONTEXT_WARNING:HIGH]")
