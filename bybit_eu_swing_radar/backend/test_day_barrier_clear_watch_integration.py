from types import SimpleNamespace

import pytest

from app.market_context_compat import install_market_context_compatibility_bridge


@pytest.mark.asyncio
async def test_compatibility_bridge_adds_barrier_watch_even_without_market_context_alerts():
    async def original(result):
        return result

    module = SimpleNamespace(enrich_market_response=original)
    install_market_context_compatibility_bridge(module)

    payload = {
        "long": {
            "symbol": "BTCUSDC",
            "side": "long",
            "category": "WATCH_ONLY",
            "decision": "NO_TRADE",
            "tradeable": True,
            "shortable": True,
            "execution_status": "DAY_TRADE_EXECUTABLE",
            "setup_score": 71.88,
            "side_direction_score": 57.6,
            "trigger": {
                "triggered": True,
                "route": "CLOSED_5M_RANGE_BREAKOUT",
                "price": 69863.5,
                "condition": "Closed 5m breakout",
            },
            "nearest_structural_barrier": 69998.4,
            "barrier_before_tp2": True,
            "target_path_valid": False,
            "expected_rr_without_barrier": 1.8,
        }
    }

    output = await module.enrich_market_response(payload)
    watch = output["long"]["barrier_clear_watch"]
    assert watch["status"] == "ARMED_BARRIER_CLEAR"
    assert watch["execution_authorized"] is False
    assert "closed 5m > 69998.4" in output["long"]["trigger"]["condition"]
