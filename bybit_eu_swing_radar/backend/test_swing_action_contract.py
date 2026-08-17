from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_compact_swing_action_contract_declares_derivatives_visibility():
    text = (ROOT / "action" / "openapi.yaml").read_text()
    compact = text.split("    CompactCandidate:", 1)[1].split(
        "    TopCandidatesResponse:", 1
    )[0]
    derivatives = text.split("    DerivativesContext:", 1)[1].split(
        "    DayTradeMarketRegime:", 1
    )[0]

    for field in (
        "derivatives:",
        "derivatives_status:",
        "derivatives_data_as_of:",
        "derivatives_context_only:",
    ):
        assert field in compact

    assert "- UNAVAILABLE" in compact
    assert "availability:" in derivatives
    assert "endpoint_errors:" in derivatives
    assert "strict_score_mutation_applied:" in derivatives
    assert "False for swing context-only enrichment." in derivatives


def test_agent_instructions_treat_compact_derivatives_as_context_only():
    text = (ROOT / "agent" / "AGENT_INSTRUCTIONS_HU.md").read_text()
    assert "`derivatives_status`" in text
    assert "`derivatives_data_as_of`" in text
    assert "hiányuk soha nem hard gate" in text
