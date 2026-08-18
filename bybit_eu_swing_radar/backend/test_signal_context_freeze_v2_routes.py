from pathlib import Path


def test_main_attaches_signal_context_freeze_v2_routes():
    text = (Path(__file__).resolve().parent / "app" / "main.py").read_text()
    assert "from app.research_signal_context_freeze_v2_api import attach_signal_context_freeze_v2_research" in text
    assert "attach_signal_context_freeze_v2_research(app, require_api_key)" in text


def test_v2_routes_hidden_and_authenticated():
    text = (Path(__file__).resolve().parent / "app" / "research_signal_context_freeze_v2_api.py").read_text()
    for route in ("/v1/research/signal-context-freeze-v2/spec", "/v1/research/signal-context-freeze-v2/capture", "/v1/research/signal-context-freeze-v2/status"):
        assert route in text
    assert text.count("dependencies=[Depends(require_api_key)]") >= 3
    assert text.count("include_in_schema=False") >= 3


def test_v2_api_enforces_prospective_lower_bound_and_no_backfill():
    text = (Path(__file__).resolve().parent / "app" / "research_signal_context_freeze_v2_api.py").read_text()
    assert "j.opened_at >= $3" in text
    assert "SELECT MIN(captured_at) FROM research_cross_layer_context_snapshots" in text
    assert '"historical_backfill_allowed": False' in text
    assert "pre_v2_journal_signals_excluded" in text
    assert "day_trade_signal_journal" in text
    for forbidden in ("net_r", "gross_r", "exit_reason", "mfe", "mae"):
        assert forbidden not in text.lower()
