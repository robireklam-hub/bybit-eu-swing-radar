from pathlib import Path


BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent


def test_openapi_exposes_v076_setup_entry_and_hard_stop_contract():
    text = (ROOT / "action" / "openapi.yaml").read_text(encoding="utf-8")
    assert "version: 0.7.6" in text
    assert "separated setup/entry state" in text
    for field in (
        "setup_state:",
        "entry_state:",
        "execution_valid:",
        "rr_valid:",
        "reference_entry:",
        "breakout_context:",
        "hard_stop:",
        "structure_invalidation:",
    ):
        assert field in text
    assert "INTRABAR_TOUCH_OR_CROSS" not in text  # runtime value, not schema-enumerated


def test_agent_must_report_valid_setup_separately_from_entry_readiness():
    text = (ROOT / "agent" / "AGENT_INSTRUCTIONS_HU.md").read_text(encoding="utf-8")
    assert "## Day-trade v0.7.6 külön szabályok" in text
    assert "setup_state=VALID" in text
    assert "BLOCKED_BY_BARRIER" in text
    assert "RR_NOT_READY" in text
    assert "ENTRY_TOO_EXTENDED" in text
    assert "ENTRY_PROVISIONAL" in text
    assert "entry_state=ENTRY_CONFIRMED" in text
    assert "hard_stop.requires_candle_close=false" in text
    assert "nem kell 5m gyertyazárást megvárni" in text
    assert "nem jár le fixen két lezárt 5m gyertya után" in text
