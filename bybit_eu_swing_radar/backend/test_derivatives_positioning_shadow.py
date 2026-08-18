from __future__ import annotations

from datetime import datetime, timezone

from research.derivatives_positioning_shadow import (
    SPEC_VERSION,
    build_snapshot,
    classify_symbol,
    funding_crowding,
    liquidation_skew,
    spec,
)


def _flow(flow_15m: str, funding: float | None = 0.0, *, coinalyze=None):
    return {
        "coverage_status": "GOOD",
        "interpretation": {"flow_15m": flow_15m, "flow_1h": flow_15m},
        "spot_context": {"return_15m_pct": 1.0, "return_1h_pct": 2.0},
        "bybit_global_derivatives": {
            "funding_rate_decimal": funding,
            "open_interest_value_quote": 10_000_000,
            "open_interest": {
                "change_5m_pct": 0.5,
                "change_15m_pct": 1.0,
                "change_1h_pct": 2.0,
                "change_4h_pct": 3.0,
            },
        },
        "coinalyze_existing": coinalyze or {},
    }


def test_spec_is_frozen_research_only() -> None:
    payload = spec()
    assert payload["version"] == SPEC_VERSION
    assert payload["research_only"] is True
    assert payload["label_free"] is True
    assert payload["live_strategy_mutated"] is False
    assert payload["promotion_allowed"] is False


def test_price_up_oi_up_maps_to_long_build() -> None:
    result = classify_symbol(
        "BTCUSDC",
        _flow("PRICE_UP_OI_UP_POSITION_BUILD"),
        {"regime": "TREND", "direction": "BULL"},
    )
    assert result["positioning_state"] == "LONG_BUILD"
    assert result["regime_interaction"] == "TREND_ALIGNED_BUILD"
    assert result["execution_proof"] is False


def test_price_down_oi_up_maps_to_short_build() -> None:
    result = classify_symbol(
        "BTCUSDC",
        _flow("PRICE_DOWN_OI_UP_POSITION_BUILD"),
        {"regime": "TREND", "direction": "BEAR"},
    )
    assert result["positioning_state"] == "SHORT_BUILD"
    assert result["regime_interaction"] == "TREND_ALIGNED_BUILD"


def test_deleveraging_and_covering_are_descriptive() -> None:
    down = classify_symbol(
        "ETHUSDC",
        _flow("PRICE_DOWN_OI_DOWN_DELEVERAGING"),
        {"regime": "EXPANSION", "direction": "BEAR"},
    )
    up = classify_symbol(
        "SOLUSDC",
        _flow("PRICE_UP_OI_DOWN_COVERING_OR_CLOSING"),
        {"regime": "HIGH_VOL_STRESS", "direction": "BULL"},
    )
    assert down["positioning_state"] == "LONG_DELEVERAGING"
    assert up["positioning_state"] == "SHORT_COVERING"
    assert down["regime_interaction"] == "VOLATILITY_UNWIND"
    assert up["regime_interaction"] == "VOLATILITY_UNWIND"


def test_funding_crowding_thresholds_are_fixed() -> None:
    assert funding_crowding(0.0001) == "POSITIVE_CROWDED"
    assert funding_crowding(-0.0001) == "NEGATIVE_CROWDED"
    assert funding_crowding(0.00009) == "NEUTRAL"
    assert funding_crowding(None) == "UNKNOWN"


def test_liquidation_skew_is_partial_safe() -> None:
    unavailable = liquidation_skew(None, None)
    assert unavailable["state"] == "UNAVAILABLE"
    assert liquidation_skew(800.0, 200.0)["state"] == "LONG_LIQ_DOMINANT"
    assert liquidation_skew(200.0, 800.0)["state"] == "SHORT_LIQ_DOMINANT"
    assert liquidation_skew(500.0, 500.0)["state"] == "BALANCED"


def test_cached_coinalyze_liquidations_are_used_without_gate() -> None:
    result = classify_symbol(
        "HYPEUSDC",
        _flow(
            "PRICE_UP_OI_UP_POSITION_BUILD",
            funding=0.0002,
            coinalyze={
                "long_liquidations_24h_usd": 900.0,
                "short_liquidations_24h_usd": 100.0,
            },
        ),
        {"regime": "RANGE", "direction": "NEUTRAL"},
    )
    assert result["funding_crowding"] == "POSITIVE_CROWDED"
    assert result["liquidations"]["state"] == "LONG_LIQ_DOMINANT"
    assert result["regime_interaction"] == "RANGE_CROWDING"
    assert result["coverage"]["liquidations"] is True


def test_missing_liquidations_remain_explicit_coverage_loss() -> None:
    result = classify_symbol(
        "ADAUSDC",
        _flow("MIXED_OR_LOW_SIGNAL"),
        {"regime": "RANGE", "direction": "NEUTRAL"},
    )
    assert result["liquidations"]["state"] == "UNAVAILABLE"
    assert result["coverage"]["liquidations"] is False
    assert result["positioning_state"] == "MIXED"


def test_snapshot_summarizes_forward_context_without_labels() -> None:
    rows = [
        classify_symbol("BTCUSDC", _flow("PRICE_UP_OI_UP_POSITION_BUILD"), {"regime": "TREND", "direction": "BULL"}),
        classify_symbol("ETHUSDC", _flow("PRICE_DOWN_OI_UP_POSITION_BUILD", -0.0002), {"regime": "COMPRESSION", "direction": "BEAR"}),
    ]
    payload = build_snapshot(
        rows,
        captured_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
        source_commit_sha="abc",
    )
    assert payload["symbol_count"] == 2
    assert payload["positioning_counts"]["LONG_BUILD"] == 1
    assert payload["positioning_counts"]["SHORT_BUILD"] == 1
    assert payload["coverage"]["flow"] == 2
    assert payload["promotion_allowed"] is False
