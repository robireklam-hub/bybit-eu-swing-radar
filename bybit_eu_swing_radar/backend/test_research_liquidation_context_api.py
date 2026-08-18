from app import research_liquidation_context_api as api


def test_regime_symbols_accepts_list_and_caps_to_usdc() -> None:
    payload = {
        "symbols": [
            {"symbol": "BTCUSDC"},
            {"symbol": "ETHUSDC"},
            {"symbol": "SOLUSDC"},
            {"symbol": "XRPUSDC"},
            {"symbol": "ADAUSDC"},
            {"symbol": "LINKUSDC"},
            {"symbol": "HYPEUSDC"},
            {"symbol": "XLMUSDC"},
            {"symbol": "DOGEUSDC"},
            {"symbol": "BTCUSDT"},
        ]
    }
    assert api._regime_symbols(payload) == [
        "BTCUSDC", "ETHUSDC", "SOLUSDC", "XRPUSDC",
        "ADAUSDC", "LINKUSDC", "HYPEUSDC", "XLMUSDC",
    ]


def test_regime_symbols_accepts_mapping() -> None:
    payload = {"symbols": {"BTCUSDC": {}, "ETHUSDC": {}, "BTCUSDT": {}}}
    assert api._regime_symbols(payload) == ["BTCUSDC", "ETHUSDC"]


def test_schema_is_dedicated_research_table() -> None:
    assert "research_liquidation_context_snapshots" in api.SCHEMA_SQL
    assert "PRIMARY KEY (spec_version, captured_hour)" in api.SCHEMA_SQL
    assert "day_trade_signal_journal" not in api.SCHEMA_SQL
