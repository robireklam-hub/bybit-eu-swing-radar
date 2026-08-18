from pathlib import Path


def test_btc_macro_research_router_attaches_btc_onchain_routes():
    path = Path(__file__).resolve().parent / "app" / "research_btc_macro_cycle_etf_api.py"
    text = path.read_text()
    assert "from app.research_btc_onchain_api import attach_btc_onchain_research" in text
    assert "attach_btc_onchain_research(app, require_api_key)" in text


def test_onchain_routes_are_hidden_and_authenticated():
    path = Path(__file__).resolve().parent / "app" / "research_btc_onchain_api.py"
    text = path.read_text()
    for route in (
        "/v1/research/btc-onchain/spec",
        "/v1/research/btc-onchain/capture",
        "/v1/research/btc-onchain/status",
    ):
        assert route in text
    assert text.count("dependencies=[Depends(require_api_key)]") >= 3
    assert text.count("include_in_schema=False") >= 3
