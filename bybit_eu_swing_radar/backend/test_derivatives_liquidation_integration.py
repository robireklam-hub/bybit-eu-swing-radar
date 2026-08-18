from pathlib import Path

from research.derivatives_positioning_shadow import classify_symbol, spec


# Connector-authored commit intentionally finalizes the PR head after route patching.
def test_classifier_consumes_dedicated_liquidation_row_shape() -> None:
    row = classify_symbol(
        "BTCUSDC",
        {
            "interpretation": {"flow_15m": "MIXED_OR_LOW_SIGNAL"},
            "bybit_global_derivatives": {"funding_rate_decimal": 0.0},
            "spot_context": {},
        },
        {"regime": "RANGE", "direction": "NEUTRAL"},
        {
            "symbol": "BTCUSDC",
            "coverage": True,
            "long_liquidations_24h_usd": 150.0,
            "short_liquidations_24h_usd": 50.0,
        },
    )
    assert row["coverage"]["liquidations"] is True
    assert row["liquidations"]["state"] == "LONG_LIQ_DOMINANT"
    assert row["liquidations"]["total_liquidations_usd"] == 200.0
    assert row["execution_proof"] is False


def test_positioning_spec_names_dedicated_liquidation_source() -> None:
    assert "liquidation-context-shadow-v1" in spec()["inputs"]["liquidations"]


def test_positioning_api_requires_bounded_nonfuture_liquidation_snapshot() -> None:
    source = Path("app/research_derivatives_positioning_api.py").read_text()
    assert "research_liquidation_context_snapshots" in source
    assert "spec_version='liquidation-context-shadow-v1'" in source
    assert "captured_at <= NOW()" in source
    assert "captured_at >= NOW() - INTERVAL '2 hours'" in source
