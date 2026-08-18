from pathlib import Path


def test_main_attaches_sector_rotation_routes():
    path = Path(__file__).resolve().parent / "app" / "main.py"
    text = path.read_text()
    assert "from app.research_sector_rotation_api import attach_sector_rotation_research" in text
    assert "attach_sector_rotation_research(app, require_api_key)" in text


def test_sector_rotation_routes_are_hidden_and_authenticated():
    path = Path(__file__).resolve().parent / "app" / "research_sector_rotation_api.py"
    text = path.read_text()
    for route in (
        "/v1/research/sector-rotation/spec",
        "/v1/research/sector-rotation/capture",
        "/v1/research/sector-rotation/status",
    ):
        assert route in text
    assert text.count("dependencies=[Depends(require_api_key)]") >= 3
    assert text.count("include_in_schema=False") >= 3


def test_provider_contract_uses_bounded_bulk_coinpaprika_sources():
    path = Path(__file__).resolve().parent / "app" / "research_sector_rotation_api.py"
    text = path.read_text()
    assert 'COINPAPRIKA_BASE_URL = "https://api.coinpaprika.com/v1"' in text
    assert 'f"{COINPAPRIKA_BASE_URL}/tickers"' in text
    assert 'f"{COINPAPRIKA_BASE_URL}/tags"' in text
    assert '{"additional_fields": "coins"}' in text
    assert "build_relative_strength_snapshot" in text


def test_no_hand_maintained_sector_mapping_is_present():
    path = Path(__file__).resolve().parent / "research" / "sector_rotation_shadow.py"
    text = path.read_text()
    assert '"hand_labels_allowed": False' in text
    assert 'TAG_TYPE = "functional"' in text
    assert "MANUAL_SECTOR" not in text
    assert "SECTOR_MAP" not in text
