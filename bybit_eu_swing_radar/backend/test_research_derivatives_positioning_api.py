from app.research_derivatives_positioning_api import (
    _extract_liquidation_context,
    _regime_symbol_map,
)


def test_extract_liquidation_context_finds_nested_symbol_derivatives() -> None:
    payload = {
        "sections": [
            {
                "items": [
                    {
                        "symbol": "BTCUSDC",
                        "derivatives": {
                            "long_liquidations_24h_usd": 100.0,
                            "short_liquidations_24h_usd": 200.0,
                        },
                    },
                    {
                        "symbol": "ETHUSDT",
                        "derivatives": {
                            "long_liquidations_24h_usd": 1.0,
                            "short_liquidations_24h_usd": 2.0,
                        },
                    },
                ]
            }
        ]
    }
    result = _extract_liquidation_context(payload, {"BTCUSDC"})
    assert set(result) == {"BTCUSDC"}
    assert result["BTCUSDC"]["short_liquidations_24h_usd"] == 200.0


def test_regime_symbol_map_accepts_canonical_list_payload() -> None:
    result = _regime_symbol_map(
        {
            "symbols": [
                {"symbol": "BTCUSDC", "regime": "RANGE", "direction": "NEUTRAL"},
                {"symbol": "SOLUSDC", "regime": "COMPRESSION", "direction": "NEUTRAL"},
            ]
        }
    )
    assert set(result) == {"BTCUSDC", "SOLUSDC"}
    assert result["SOLUSDC"]["regime"] == "COMPRESSION"


def test_regime_symbol_map_is_tolerant_of_dict_representation() -> None:
    result = _regime_symbol_map(
        {"symbols": {"BTCUSDC": {"regime": "TREND", "direction": "BULL"}}}
    )
    assert result["BTCUSDC"]["symbol"] == "BTCUSDC"
    assert result["BTCUSDC"]["regime"] == "TREND"
