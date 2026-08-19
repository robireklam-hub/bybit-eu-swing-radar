from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_main_attaches_signal_context_freeze_v3_routes():
    text = (ROOT / "app" / "main.py").read_text()
    assert "from app.research_signal_context_freeze_v3_api import attach_signal_context_freeze_v3_research" in text
    assert "attach_signal_context_freeze_v3_research(app, require_api_key)" in text


def test_v3_api_is_immutable_history_only_and_prospective():
    text = (ROOT / "app" / "research_signal_context_freeze_v3_api.py").read_text()
    for route in ("/v1/research/signal-context-freeze-v3/spec", "/v1/research/signal-context-freeze-v3/capture", "/v1/research/signal-context-freeze-v3/status"):
        assert route in text
    assert text.count("dependencies=[Depends(require_api_key)]") >= 3
    assert "FROM research_snapshot_history" in text
    assert "research_family='cross-layer-context-v2'" in text
    assert "captured_at <= $2" in text
    assert "SELECT MIN(captured_at)" in text
    assert "research_cross_layer_context_snapshots" not in text
    assert '"historical_backfill_allowed": False' in text
    assert "pre_v3_journal_signals_excluded" in text
    for forbidden in ("net_r", "gross_r", "exit_reason", "mfe", "mae"):
        assert forbidden not in text.lower()
