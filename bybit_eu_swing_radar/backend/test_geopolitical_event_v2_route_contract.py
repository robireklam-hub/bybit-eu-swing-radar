from pathlib import Path


def test_main_attaches_geopolitical_event_v2_routes():
    path = Path(__file__).resolve().parent / "app" / "main.py"
    text = path.read_text()
    assert "from app.research_geopolitical_event_v2_api import attach_geopolitical_event_v2_research" in text
    assert "attach_geopolitical_event_v2_research(app, require_api_key)" in text


def test_v2_routes_are_hidden_and_authenticated():
    path = Path(__file__).resolve().parent / "app" / "research_geopolitical_event_v2_api.py"
    text = path.read_text()
    for route in (
        "/v1/research/geopolitical-event-v2/spec",
        "/v1/research/geopolitical-event-v2/capture",
        "/v1/research/geopolitical-event-v2/status",
    ):
        assert route in text
    assert text.count("dependencies=[Depends(require_api_key)]") >= 3
    assert text.count("include_in_schema=False") >= 3


def test_v2_uses_static_event_export_not_doc_api():
    path = Path(__file__).resolve().parent / "app" / "research_geopolitical_event_v2_api.py"
    text = path.read_text()
    assert "lastupdate.txt" in text
    assert ".export.CSV.zip" in text
    assert "zipfile.ZipFile" in text
    assert "TimelineVolRaw" not in text
    assert "api.gdeltproject.org/api/v2/doc/doc" not in text


def test_v1_workflow_is_manual_only():
    path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "geopolitical-risk-shadow.yml"
    text = path.read_text()
    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "branches: [main]" not in text


def test_v2_does_not_read_live_trade_state_or_outcomes():
    root = Path(__file__).resolve().parent
    core = (root / "research" / "geopolitical_event_shadow_v2.py").read_text().lower()
    api = (root / "app" / "research_geopolitical_event_v2_api.py").read_text().lower()
    for forbidden in (
        "day_trade_journal",
        "net_r",
        "profit_factor",
        "setup_score",
        "shortable",
        "borrowability",
        "strict_eligible",
    ):
        assert forbidden not in core
        assert forbidden not in api
