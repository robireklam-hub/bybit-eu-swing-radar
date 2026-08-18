from app.research_relative_strength_api import _select_universe


def test_select_universe_is_usdc_non_stable_and_btc_anchored() -> None:
    tickers = [
        {"symbol": "ETHUSDC", "turnover24h": "900"},
        {"symbol": "SOLUSDC", "turnover24h": "800"},
        {"symbol": "USDTUSDC", "turnover24h": "10000"},
        {"symbol": "EURUSDC", "turnover24h": "9000"},
        {"symbol": "BTCUSDC", "turnover24h": "100"},
        {"symbol": "XRPUSDC", "turnover24h": "700"},
        {"symbol": "BTCUSDT", "turnover24h": "99999"},
    ]
    symbols = _select_universe(tickers, limit=3)
    assert symbols == ["ETHUSDC", "SOLUSDC", "BTCUSDC"]
    assert all(symbol.endswith("USDC") for symbol in symbols)
    assert "USDTUSDC" not in symbols
    assert "EURUSDC" not in symbols


def test_select_universe_requires_btc_anchor() -> None:
    tickers = [{"symbol": "ETHUSDC", "turnover24h": "900"}]
    try:
        _select_universe(tickers, limit=20)
    except RuntimeError as exc:
        assert "BTCUSDC" in str(exc)
    else:
        raise AssertionError("expected BTC anchor failure")
