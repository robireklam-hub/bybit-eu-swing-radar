from pathlib import Path


def test_main_attaches_cross_layer_v2_routes():
    text = (Path(__file__).resolve().parent / "app" / "main.py").read_text()
    assert "from app.research_cross_layer_context_v2_api import attach_cross_layer_context_v2_research" in text
    assert "attach_cross_layer_context_v2_research(app, require_api_key)" in text


def test_v2_routes_hidden_and_authenticated():
    text = (Path(__file__).resolve().parent / "app" / "research_cross_layer_context_v2_api.py").read_text()
    for route in ("/v1/research/cross-layer-context-v2/spec", "/v1/research/cross-layer-context-v2/capture", "/v1/research/cross-layer-context-v2/status"):
        assert route in text
    assert text.count("dependencies=[Depends(require_api_key)]") >= 3
    assert text.count("include_in_schema=False") >= 3


def test_v2_source_map_contains_new_layers_and_keeps_v1_sources():
    text = (Path(__file__).resolve().parent / "app" / "research_cross_layer_context_v2_api.py").read_text()
    assert '"sector_rotation": ("research_sector_rotation_snapshots", "sector-rotation-shadow-v1")' in text
    assert '"btc_onchain": ("research_btc_onchain_snapshots", "btc-onchain-context-shadow-v1")' in text
    assert '"eth_onchain": ("research_eth_onchain_snapshots", "eth-onchain-context-shadow-v1")' in text
    assert '"relative_strength": ("research_relative_strength_snapshots", "relative-strength-shadow-v1")' in text
