from __future__ import annotations

from datetime import datetime, timezone

from app.models import ScanResponse
from app.swing_api_views import (
    compact_swing_scan,
    is_research_full_scan_request,
    select_fresh_symbol_setup,
)
from app.swing_candidate_context import _derivatives_status


NOW = datetime(2026, 8, 17, 12, 30, tzinfo=timezone.utc)


def _setup(
    symbol: str,
    side: str,
    score: float,
    execution_status: str,
    *,
    derivatives: dict | None = None,
) -> dict:
    return {
        "symbol": symbol,
        "side": side,
        "state": "WATCH" if execution_status != "EXECUTION_ELIGIBLE" else "ARMED",
        "grade": "WATCH" if execution_status != "EXECUTION_ELIGIBLE" else "B",
        "confidence": "MEDIUM",
        "last_price": 1.0,
        "shortable": side == "short",
        "execution_modes": ["spot_margin"] if side == "short" else ["spot"],
        "setup_type": "TEST",
        "thesis": ["large narrative that belongs in the detailed symbol endpoint"],
        "expansion_score": 60.0,
        "direction_score": -40.0 if side == "short" else 40.0,
        "quality_score": 65.0,
        "setup_score": score,
        "expected_rr": 2.0,
        "metrics": {
            "tradeable": execution_status == "EXECUTION_ELIGIBLE",
            "execution_status": execution_status,
            "turnover_24h_usdc": 250000.0,
            "spread_bps": 10.0,
            "volume_ratio_4h": 1.4,
            "liquidity_reasons": [],
            "derivatives": derivatives or {"very_large_context": "x" * 5000},
            "unneeded_internal_metric": "x" * 5000,
        },
        "bullish_scenario": "detailed bullish scenario",
        "bearish_scenario": "detailed bearish scenario",
        "weakest_point": "test",
        "risks": ["detailed risk"],
        "data_quality": "GOOD",
        "missing_data": [],
        "data_as_of": NOW,
    }


def _scan() -> ScanResponse:
    regime = {
        "data_as_of": NOW,
        "data_quality": "GOOD",
        "btc_regime": "range",
        "volatility_regime": "normal",
        "preferred_side": "neutral",
    }
    return ScanResponse.model_validate(
        {
            "data_as_of": NOW,
            "data_as_of_budapest": "2026-08-17T14:30:00+02:00",
            "data_quality": "GOOD",
            "market_regime": regime,
            "longs": [_setup("BTCUSDC", "long", 80.0, "EXECUTION_ELIGIBLE")],
            "shorts": [_setup("ONDOUSDC", "short", 70.0, "EXECUTION_ELIGIBLE")],
            "extended_watchlist": [_setup("ONDOUSDC", "long", 90.0, "WATCH_ONLY")],
            "liquidity_blocked": [_setup("WLDUSDC", "long", 75.0, "LIQUIDITY_BLOCKED")],
            "momentum_radar": [{"payload": "x" * 10000}],
            "universe_stats": {"analyzed": 30},
            "coverage": {"coinalyze": "context"},
            "exclusions": [{"symbol": "ALTUSDC", "reason": "x" * 10000}],
        }
    )


def test_agent_scan_is_compact_without_mutating_full_cached_scan() -> None:
    full = _scan()
    compact = compact_swing_scan(full)

    assert compact.extended_watchlist == []
    assert compact.liquidity_blocked == []
    assert compact.momentum_radar == []
    assert compact.exclusions == []
    assert compact.longs[0].thesis == []
    assert compact.longs[0].risks == []
    assert compact.longs[0].bullish_scenario is None
    assert compact.longs[0].bearish_scenario is None
    assert "derivatives" not in compact.longs[0].metrics
    assert "unneeded_internal_metric" not in compact.longs[0].metrics
    assert compact.longs[0].metrics["execution_status"] == "EXECUTION_ELIGIBLE"

    # Full research snapshot remains byte-for-byte semantically available to the
    # caller that asks for it; compacting is a copied view only.
    assert len(full.extended_watchlist) == 1
    assert len(full.liquidity_blocked) == 1
    assert len(full.momentum_radar) == 1
    assert "derivatives" in full.longs[0].metrics


def test_only_swing_liquidity_collector_user_agent_gets_full_scan_contract() -> None:
    assert is_research_full_scan_request("swing-liquidity-shadow/1") is True
    assert is_research_full_scan_request("swing-liquidity-shadow/2") is True
    assert is_research_full_scan_request("bybit-eu-swing-agent/1") is False
    assert is_research_full_scan_request(None) is False


def test_symbol_setup_is_selected_from_current_scan_with_worker_preference_rule() -> None:
    scan = _scan()
    selected = select_fresh_symbol_setup(scan.model_dump(mode="json"), "ondousdc")

    assert selected is not None
    assert selected.symbol == "ONDOUSDC"
    # Executable current setup wins over a higher-score WATCH_ONLY opposite side,
    # mirroring the worker's existing cache preference without reading stale cache.
    assert selected.side == "short"
    assert selected.setup_score == 70.0
    assert selected.data_as_of == NOW

    blocked = select_fresh_symbol_setup(scan.model_dump(mode="json"), "WLDUSDC")
    assert blocked is not None
    assert blocked.data_as_of == NOW
    assert blocked.side == "long"


def test_partial_derivatives_reason_lists_every_missing_value_field() -> None:
    payload = {
        "open_interest_usd": 123456.0,
        "oi_change_1h_pct": 1.0,
        "oi_change_4h_pct": 2.0,
        "oi_change_24h_pct": None,
        "funding_rate": 0.0001,
        "long_liquidations_24h_usd": None,
        "short_liquidations_24h_usd": None,
        "availability": {
            "current_oi": True,
            "funding": True,
            "oi_history": True,
            "liquidations": False,
        },
        "endpoint_errors": [],
    }
    status, reason = _derivatives_status(payload)

    assert status == "PARTIAL"
    assert "oi_change_24h_pct" in reason
    assert "long_liquidations_24h_usd" in reason
    assert "short_liquidations_24h_usd" in reason
    assert "liquidations" in reason
