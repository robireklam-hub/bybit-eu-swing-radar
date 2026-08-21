from __future__ import annotations

import pytest

from research import swing_liquidity_lifecycle as lifecycle
from scripts.production_swing_liquidity_lifecycle_smoke import validate_lifecycle_persistence


@pytest.mark.asyncio
async def test_lineage_state_is_fail_closed_before_development_evidence(monkeypatch):
    async def lineage_status(conn, study, *, entity_type, entity_id):
        assert study == lifecycle.STUDY
        assert entity_type == "TRIAL"
        assert entity_id == lifecycle.STUDY
        return {"event_count": 4, "current_event_type": "LINEAGE_RECORDED"}

    async def fail_record(*args, **kwargs):
        raise AssertionError("LINEAGE must not auto-advance into DEVELOPMENT")

    monkeypatch.setattr(lifecycle, "lifecycle_status", lineage_status)
    monkeypatch.setattr(lifecycle, "record_trial_event", fail_record)

    result = await lifecycle.record_lifecycle_on_capture_persistence(
        object(), inserted_capture=True, source_commit_sha="a" * 40
    )

    assert result["event_type"] == "LINEAGE_RECORDED"
    assert result["inserted"] is False
    assert result["reason"] == "waiting_for_development_maturity_gate"
    development = result["development"]
    assert development["required_matured_event_count"] == 60
    assert development["validation_target_matured_event_count"] == 40
    assert development["maturity_source"] == "label_blind_forward_event_status"
    assert development["development_evidence_recorded"] is False
    assert development["outcome_visible"] is False
    assert development["threshold_search_allowed"] is False
    assert development["promotion_allowed"] is False
    assert result["live_strategy_mutated"] is False
    assert result["production_eligibility_mutated"] is False
    assert result["execution_authorized"] is False


def _valid_persistence_result() -> dict:
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "study": lifecycle.STUDY,
        "inserted": True,
        "lifecycle_adoption": {
            "attempted": True,
            "inserted": False,
            "event_type": "LINEAGE_RECORDED",
            "reason": "waiting_for_development_maturity_gate",
            "prospective_adoption": True,
            "historical_backfill": False,
            "research_only": True,
            "live_strategy_mutated": False,
            "production_eligibility_mutated": False,
            "execution_authorized": False,
            "development": {
                "required_matured_event_count": 60,
                "validation_target_matured_event_count": 40,
                "maturity_source": "label_blind_forward_event_status",
                "development_evidence_recorded": False,
                "outcome_visible": False,
                "threshold_search_allowed": False,
                "promotion_allowed": False,
            },
        },
    }


def test_production_smoke_accepts_only_closed_development_gate_contract():
    assert validate_lifecycle_persistence(_valid_persistence_result()) == []

    tampered_target = _valid_persistence_result()
    tampered_target["lifecycle_adoption"]["development"]["required_matured_event_count"] = 59
    assert "development_target_mismatch" in validate_lifecycle_persistence(tampered_target)

    leaked_outcome = _valid_persistence_result()
    leaked_outcome["lifecycle_adoption"]["development"]["outcome_visible"] = True
    assert "development_outcome_visible_invalid" in validate_lifecycle_persistence(leaked_outcome)

    enabled_search = _valid_persistence_result()
    enabled_search["lifecycle_adoption"]["development"]["threshold_search_allowed"] = True
    assert "development_threshold_search_allowed_invalid" in validate_lifecycle_persistence(enabled_search)
