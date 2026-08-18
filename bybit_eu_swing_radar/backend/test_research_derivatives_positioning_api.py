from app.research_derivatives_positioning_api import _extract_liquidation_context


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
